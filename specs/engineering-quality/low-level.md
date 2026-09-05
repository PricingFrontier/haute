# Engineering Quality — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `pyproject.toml` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); configures Ruff, pytest, coverage, critical backend coverage thresholds, mypy, pinned development tools, and excludes non-product directories from the normal Ruff target. |
| `AGENTS.md` | Records repository-local engineering and review instructions for contributors and coding agents; it is guidance, not executable build or test configuration. |
| `CLAUDE.md` | Directs Claude-compatible coding agents to the repository's authoritative `AGENTS.md` engineering instructions without duplicating policy. |
| `.gitignore` | Excludes generated builds, virtual environments, caches, local pipeline output/data, tool state, and other non-source artifacts from normal version-control discovery. |
| `specs/corpus.toml` | Versioned declaration for supported component documents outside the conventional high/low pair; records a closed document kind and exact required headings. |
| `specs/ownership.toml` | Machine-checked ledger for files shared by multiple Module maps or explicit cross-component prose ownership claims; records the single primary owner and all consumer components. |
| `.pre-commit-config.yaml` | Runs Ruff fix/format plus local mypy and frontend typecheck/lint hooks on relevant source changes. |
| `.github/workflows/ci.yml` | Defines PR/main CI jobs: canary, install/package smoke, dependency floors, static/type checks, coverage shards/gate, compatibility/probe, performance, optional dependencies, platform, mutation-config, frontend, and browser E2E lanes. Branch protection, outside this repository workflow file, determines which checks are required for merge. |
| `.github/workflows/dependencies.yml` | Runs the weekly/manual fresh unlocked-resolve monitor plus the locked Python/frontend advisory gate, the latter daily as well as on manual dispatch, main lock-policy changes, and relevant PRs; retains failed reports and raises/updates dependency-watch issues on every non-pull-request failure. |
| `.github/workflows/frontend-shuffle.yml` | Runs the scheduled/manual shuffled Vitest monitor and raises/updates shuffle-watch issues with its seed on eligible failures. |
| `.github/workflows/mutation.yml` | Plans changed mutation targets, runs separate CI-job shards whose mutants execute serially per runner, and uses a failure-aware non-cancelled status condition on the single merge gate so plan/shard failures become failed rather than skipped checks. |
| `.github/workflows/performance.yml` | Runs scheduled/manual Python and browser-performance lanes and uploads their artifacts. |
| `frontend/package.json` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); defines frontend lint/type/unit/coverage/bundle/E2E/benchmark command entry points and frontend critical-coverage entries. |
| `frontend/scripts/generate-api-contracts.mjs` | Pinned schema-to-browser generator: selects each pilot's exact transitive definition closure, emits reviewed TypeScript declarations and constants, and bundles separate self-contained Ajv standalone ESM validators so Explore validation remains lazy. The eager execution validator co-exports its schema-version literal while Explore-only option constants remain in the lazy contract module. Its check mode compares every output byte without writing. |
| `frontend/scripts/generate-api-contracts.test.mjs` | Isolated generator contract: regenerates outside the repository dependency tree, imports both standalone validators without runtime Ajv, and proves that independently staling every generated output makes check mode fail without modifying the file. |
| `frontend/src/generated/api-contracts.schema.json`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/generated/api-contracts.constants.generated.ts`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.mjs`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.d.mts`, `frontend/src/generated/api-contracts.explore-charts.validators.mjs`, and `frontend/src/generated/api-contracts.explore-charts.validators.d.mts` | Committed generated contract bundle, static declarations, lazy Explore constants, and split standalone runtime validators; the execution validator and declaration also export its schema version. They are reviewed build inputs, never edited by hand or regenerated at application runtime. |
| `frontend/eslint.config.js` | Defines blocking browser TypeScript/React ESLint rules, fourteen explicit pre-existing file/rule exceptions, generated-report ignores, and underscore-prefixed intentionally-unused names. |
| `frontend/vitest.config.ts` | Configures the Vitest unit-test environment, setup, source/test selection, coverage reporting, and blocking 80/75/80/80 global thresholds. |
| `frontend/playwright.config.ts` | Configures serial browser E2E projects, retries, artifacts, and readiness-managed local E2E server. |
| `frontend/e2e/browserInteractions.ts` | Shared Playwright helpers for app-level modifier shortcuts and React Flow submodel double-click dispatch. |
| `frontend/e2e/core-flows.spec.ts` | Playwright coverage for core browser flows. |
| `frontend/e2e/canvas-assurance.spec.ts` | Deterministic Chromium coverage and visual baselines for mixed Banding-to-Rating persistence and optimiser result/apply/MLflow-boundary journeys. |
| `frontend/e2e/data-io-nodes.spec.ts` | Playwright coverage for data-I/O node browser flows. |
| `frontend/e2e/edge-join.spec.ts` | Deterministic full-browser Edge Join workflow: compatible-edge feedback and insertion, configuration/preview, save/reload topology, repeated joins, named API-input source-handle preservation, immediate unsaved-submodel drill/render identity coverage, and downstream trace highlighting. |
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
| `scripts/check_changed_coverage.py` | Intersects a Git new-file-line diff with Coverage.py format-3 statement and branch-arc evidence, enforcing 100% changed executable coverage for the configured execution-critical source surface. |
| `scripts/check_dependency_audit.py` | Stdlib-only fail-closed advisory-policy orchestrator/parser: exports the exact locked Python graph, runs pinned `pip-audit` and full-tree `npm audit`, validates report schemas, gives npm meta-findings a topology-independent transitive identity, and subtracts only current exact accepted-risk entries. |
| `security/accepted-risks.toml` | Versioned exact advisory acceptance registry. Entries require ecosystem/package/advisory identity, owner, exposure, compensating control, approval date, and non-expired review date; stale, duplicate, malformed, mismatched, or unused entries fail the audit. |
| `scripts/core_test_files.txt` | Curated core test-subset manifest and its selection/refresh rationale for canary/dependency lanes. |
| `scripts/e2e_git_topologies.py` | Exercises Git topology scenarios used by repository-level verification. |
| `scripts/generate_api_contracts.py` | Deterministically composes the execution-strategy diagnostic and Explore chart root models into one Draft 2020-12 JSON Schema bundle, fixes the recursive finite-JSON definition, and atomically writes or byte-checks the committed frontend source artifact. |
| `scripts/extract_polars_io.py` | Extracts the Polars I/O argument schema by introspection, and with `--diff` reports installed-versus-committed drift as a non-failing Markdown freshness summary. |
| `scripts/init_smoke.py` | Builds/installs or consumes a wheel in a fresh environment, initialises a project, serves it headlessly, exercises an authenticated endpoint, and shuts it down. |
| `scripts/memory_smoke.py` | Runs the maintained memory-safety smoke path. |
| `scripts/package_smoke_check.py` | Validates an installed distribution's package/runtime expectations. |
| `scripts/update_assistant_example_manifests.py` | Checks or explicitly refreshes closed content-addressed assistant example inventories; unsafe, duplicate, missing, and undeclared paths fail in both modes. |
| `scripts/preflight.ps1` | Windows preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/preflight.sh` | POSIX preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/regen_sanitize_parity_fixture.py` | Regenerates the retained backend compatibility golden when deliberately requested. |
| `scripts/run_frontend_e2e_server.py` | Generates the isolated browser fixture, then starts and readiness-signals its dedicated-port backend and Vite proxy for Playwright. |
| `scripts/run_assistant_evaluation.py` | Fail-closed credentialed assistant qualification command: loads a closed candidate/matrix/scenario set, invokes an explicit live runner repeatedly, writes a redacted atomic report, and succeeds only for an already-qualified configuration that still meets every threshold. |
| `scripts/run_mutation_pytest.py` | Runs a mutation witness command from a fresh synthetic project while retaining repository pytest configuration and placing pytest inputs in a sibling temporary boundary, so relative Haute runtime state cannot leak between mutants or alter path-confinement semantics. |
| `scripts/run_mutation_suite.py` | Implements mutation target selection, work planning, shard execution, merge, and survival-threshold reporting. |
| `scripts/run_perf_suite.py` | Runs bounded Python performance tests and writes schema-4 workload, environment, resource, wall-time, and per-test evidence artifacts. |
| `scripts/spec_corpus_inventory.py` | Recursively classifies every supported-suffix specification file, rejects undeclared nested documents, builds the exact working-tree content fingerprint, validates complete per-file review coverage, and derives component/supplemental/governance/roadmap totals. |
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
| `tests/test_assistant_example_portfolio.py` | Ordinary specialist evidence for the packaged assistant portfolio: source parity, trace/dry-run, real training/scoring and optimisation/apply, deployment preflight, and adversarial rejection. |
| `tests/test_api_contract_generation.py` | Backend contract-generation evidence: deterministic Pydantic-to-schema output, stale/read-only check behaviour, root/definition ownership, recursive finite JSON, safe-integer bounds, collection caps, and pinned frontend code-generation dependencies. |
| `tests/fixtures/` | Checked-in input, golden, expected-contract, UI-contract, and data fixtures consumed by active tests. |
| `tests/performance/` | `perf`-marked benchmark-style tests excluded from ordinary pytest and run by the performance harness, including the rating miss-guard evidence matrix (`test_rating_miss_guard_perf.py`). |
| `tests/performance/test_execution_engine_certification.py` | Reproducible execution-engine certification scenarios for isolated wide-Parquet projection memory, modelling-menu demand, per-port API-input projection, and checkpoint-bounded direct JSONL shredding. |
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
| `specs/roadmap/bug-findings-2026-09-05.md`, `specs/roadmap/test-gap-findings-2026-09-05.md` | Dated supporting findings for the test expansion programme; preserve the review snapshot and recorded evidence without creating another delivery queue. |
| `tests/workflow_coverage.toml` | Versioned workflow coverage ledger: workflow families, node types, and scenario records with coverage state, owning package, test references, and execution evidence. |
| `tests/test_workflow_coverage.py` | Ledger validator (`load_ledger`, `roadmap_package_ids`, `ledger_violations`) and its malformed-ledger cases; a meta-marked repository-health module. |
| `tests/_test_debt_scanner.py` | Test-debt scanning primitives (backend AST visitor, frontend source scanner, frontend test-file predicate) shared by `tests/test_test_debt.py` and `tests/test_workflow_coverage.py`; a support module, not a test. |
| `repro/` | Point-in-time benchmark/reproduction programs and metadata; not an automatically current product-behaviour contract. |
| `mlflow.db` | Checked-in SQLite MLflow tracking-store snapshot (experiments, runs, metrics, parameters, tags, and model-version metadata). It is repository data/local state, not an installed-package input or a runtime prerequisite; MLflow may instead use the configured tracking store. |

## Key types and data structures

- **Backend critical-coverage entry** in `pyproject.toml` has a source `path`,
  minimum statement/branch percentages, and rationale. The coverage JSON path
  is `.cache/coverage/backend.json`.
- **Changed-code coverage configuration** in `pyproject.toml` names the exact
  execution-critical source paths governed by the gate and fixes both changed
  statement and changed branch coverage at 100%. A branch arc is in scope when
  either positive source-code endpoint is a changed new-file line. The checker
  unions the merge-base-to-HEAD diff, current tracked worktree changes, and
  untracked configured source files so local and clean-CI runs apply the same
  executable-line contract.
- **Frontend critical-coverage entry** in `frontend/package.json` has a source
  glob-like `pattern` and thresholds for statements, branches, functions, and
  lines; the summary artifact is `coverage/coverage-summary.json`. This sits
  after Vitest's global floors of 80% statements, 75% branches, 80% functions,
  and 80% lines.
- **Generated API contract pipeline** has two explicit reviewed stages. The
  Python stage maps canonical Pydantic root models to a deterministic Draft
  2020-12 bundle. The Node stage computes the exact local-definition closure for
  each pilot and emits static declarations/constants plus one standalone runtime
  validator per lazy-loading boundary. Generated validation owns structural
  assertions only; feature adapters own semantic relationships and stable error
  policy.
- **Documentation violation** is the ordered
  `document<TAB>rule<TAB>detail` record emitted by
  `tests/test_docs_accuracy.py`. The committed
  `tests/docs_accuracy_baseline.txt` contains every accepted current record
  exactly once in sorted order.
- **Supplemental specification declaration** in `specs/corpus.toml` has a
  canonical repository-root-relative component Markdown path, the closed kind
  `decision`, and a non-empty unique list of exact level-two headings. The
  declared path must exist outside the conventional high/low pair; unknown
  kinds, traversal, duplicates, malformed headings, and undeclared `.md` or
  `.toml` files fail inventory.
- **Mutation target** in `mutation/targets.json` selects a Cosmic Ray config,
  witness test command, survivor budget, rationale, and required positive-integer
  `max_pending_per_shard`. The cap is calibrated per target to retain CI
  timeout and artifact-upload headroom, rather than assuming one global workload
  fits every target. `scripts/run_mutation_suite.py` rejects malformed target
  metadata and carries the rationale and cap into plan and aggregate result
  artifacts.
- **Test-health summary** — the backend AST scanner includes
  `pytest.mark.flaky` in its exact fingerprint budget (zero at present). The
  generated Markdown groups live site counts by signal and lists each mutation
  target separately, so a reviewer can act without reading the scanner's
  implementation. Its full-corpus regeneration assertion has a 180-second
  per-test ceiling so the sharded coverage lane retains bounded failure while
  allowing for instrumentation and runner contention. The Playwright CI retry
  allowance is pinned to exactly 2 by a direct assertion against
  `frontend/playwright.config.ts`.
- **Performance report schema 4** contains top-level `environment`, `workload`,
  `resources`, and `wall_time` records plus per-test bounded evidence.
  Unavailable numeric counters are JSON `null`; reported pytest phase time plus
  runner overhead must equal total time within the recorded tolerance. The
  deterministic CI-small Polars scenario records every `ExecutionProfile`,
  while 1m/10m inputs remain opt-in. Child peak RSS uses live samples from the
  exact child PID whenever any were observed. The process-global, sticky
  `RUSAGE_CHILDREN` high-water mark is only a no-sample fallback, so a previous
  child cannot contaminate a later scenario's measurement.
- **Playwright configuration** uses a single worker and `fullyParallel: false`;
  Chromium is the normal project and Firefox is restricted to `@smoke` tests.
  CI retries twice, recording traces on first retry and screenshots/video on
  failure.
- **Edge Join E2E fixture** is a project-isolated, generated pipeline with
  deterministic small frames and one API-input frame whose raw label is the
  persisted source handle. The workflow targets nodes, handles, and rendered
  edge ids through stable locators and derives drag coordinates from live
  handle bounds and rendered SVG path geometry; it does not use production
  data, fixed sleeps, or hard-coded canvas coordinates. The same fixture also
  groups the complete graph and immediately drills into the unsaved definition,
  proving its API Input retains an authoritative rendered source handle.
- **Pytest configuration** constrains collection to `tests/`, has strict
  markers/configuration/xfails, excludes `perf` by default, and recognises
  `slow`, `perf`, and `sandbox_strict` markers.
- **Component improvement package** is a `### <package-id>` section in one
  flat `specs/roadmap/<component>.md` file. It has a stable ID, priority/order,
  problem, plan, acceptance criteria, dependencies, and current evidence. The
  component file owns whether the package is queued, blocked, or retired. Its
  priority row and heading are a checked one-to-one pair; delivered package
  histories and review-outcome narratives do not remain in the active queue.
- **Workflow coverage ledger** in `tests/workflow_coverage.toml` (`version = 1`)
  has `workflows` (an id in the form W01, title, component directories, the
  specification `documents` it exercises, entry points), `node_types` (every
  node type value with its workflows), and `scenarios` (an id in the form
  W01-S01, workflow, a `contract` of the form
  `specs/<component>/<file>.md#<heading-slug>` naming the section that states
  the invariant, invariant, optional finding id, state, entry point, tier, lane,
  real and stubbed dependency names, and test references). A `covered`
  scenario carries an `evidence` table with the tested commit, exact command,
  and result; a `not-applicable` scenario carries a reason; `gap` and
  `decision` scenarios name the owning roadmap package. Test references are
  pytest node ids
  (`tests/<file>.py::[Class::]test`) or frontend titles
  (`frontend/<file>.test.ts::<title>`, also `.test.tsx` and Playwright
  `.spec.ts`). Entry points are file, cli, http, browser, hosted, scoring, and
  library; tiers are unit, route, workflow, browser, property, and process;
  lanes are backend, frontend, browser, platform, package, perf, and mutation.

## Control flow

1. A normal contributor pre-commit path applies Ruff fix/format and runs the
   local type/lint hooks selected by changed paths. CI remains the authority for
   the full matrix and clean environments.
2. `.github/workflows/ci.yml` synchronises the locked dev environment. Its
   canary runs Ruff then the manifest in `scripts/core_test_files.txt`; static
   CI runs Ruff, mypy, and `HAUTE_BUILD_FRONTEND=1 uv build`.
3. Backend coverage runs the full test corpus in two pytest-split shards. The
   gate combines the coverage files, enforces the global 90% floor, writes JSON,
   invokes `scripts/check_critical_coverage.py` for per-file floors, then invokes
   `scripts/check_changed_coverage.py` against the pull-request base SHA (or the
   preceding main revision for a push). The coverage-gate checkout contains the
   required history; an unreadable base revision is a gate failure rather than an
   empty-diff pass.
4. Compatibility, optional-dependency, platform, package, init, and mutation
   configuration smoke lanes run their named commands. The 3.14 probe is
   explicitly allowed to fail without blocking the workflow result.
5. The frontend CI job runs `npm ci` then the frontend-only preflight. Every
   frontend preflight mode runs `npm run check:contracts` before type checking;
   the backend core subset separately proves the Pydantic-to-schema stage through
   `tests/test_api_contract_generation.py`. Ordinary regeneration runs the Python
   stage followed by the Node stage, while application build and runtime only
   consume the committed results. Browser
   E2E additionally synchronises Python, installs Chromium/Firefox, and runs
   `npm run test:e2e`; Playwright calls `scripts/run_frontend_e2e_server.py` and
   waits for its readiness URL. The harness preserves the same-origin Vite
   proxy and real HttpOnly-cookie bootstrap used by the product; it never
   exports a browser-readable session-token variable or adds a browser bearer
   header. Its generated fixture explicitly owns the deterministic
   `raw_rows` → `enriched` → `priced` core graph and sidecars after invoking
   the blank `haute init` scaffold; browser coverage must not depend on product
   scaffolding to supply test nodes. The fixture keeps all standard imports
   before the pipeline constructor so the parser retains externally inserted
   preamble code and websocket file sync can refresh the Imports editor. Its
   out-of-browser readiness probe uses the supported non-browser token header.
   `HAUTE_E2E_BACKEND_PORT`,
   `HAUTE_E2E_FRONTEND_PORT`, and
   `HAUTE_E2E_READINESS_PORT` may select alternate loopback ports when a
   developer already has Haute running; the harness and Playwright config
   validate and share those values.
   `frontend/scripts/check-bundle-size.mjs` counts the production entry and
   modulepreload chunks against default ceilings of 283 KiB initial and
   1,333 KiB total JavaScript gzip. The measured bundle is approximately
   281.1 KiB initial and 1,328.3 KiB total with the eager execution-diagnostic
   validator, server-owned editor identities, extracted graph/job controllers,
   the collapsed submodel input socket's canonical port resolution and
   parent-binding projection, and existing recovery/live-sync boundaries. Each
   ceiling raise names the eager core it admits and restores roughly 2 KiB of
   headroom, so the ratchet keeps catching an accidental eager import rather
   than only the change that happens to cross it. The Explore chart
   validator is a separate lazy artifact in the chart-config chunk rather than
   an entry modulepreload.
   Modelling training response parsers remain in a dynamically imported
   `types/trainGuards.ts` chunk. The checker classifies that chunk as lazy-only
   and fails if it becomes a startup modulepreload; CI may override the ceiling
   only through the documented
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
   to enforce total survivor budgets. Planning uses each target's required
   `max_pending_per_shard` cap: pending means executable mutants only, shard count
   is `max(1, ceil(pending / cap))`, and no plan may require more than GitHub
   Actions' 256-job matrix limit. The cap is calibrated to retain timeout and
   artifact-upload headroom; it must not be weakened by silently overpacking a
   target. The JSON shred target uses at most 20 mutants per shard against its
   90-second expanded witness ceiling, and the shard job has a 40-minute hard
   limit so the 30-minute worst-case test budget still leaves setup and artifact
   headroom. The merge job's `!cancelled()` status
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
8. A specification inventory recursively enumerates every `.md` and `.toml`
   below `specs/`, classifies the conventional component pairs and root/roadmap
   documents, validates `specs/corpus.toml`, then rejects any remaining path.
   Coverage is loaded only after that literal set is closed, so an undeclared
   document cannot evade either the snapshot or coverage ledger.

## Edge cases and invariants

- Coverage data uses relative paths so artifacts from separate runner checkout
  paths can be combined correctly. Shards disable the immediate fail-under
  check; only the combine gate is authoritative.
- Changed-code coverage normalises Windows/POSIX separators and Git rename paths,
  ignores deletions and changed non-executable lines, treats every executable line
  in an untracked configured Python file as new, and fails if a changed configured
  source file is absent from the branch-enabled coverage artifact. A diff containing
  no changed executable target is a reported no-op, not fabricated 100% evidence.
- The dependency-floor job deliberately re-resolves at `lowest-direct` and uses
  `--frozen` thereafter; it must not silently re-lock at the normal highest
  resolution. The scheduled dependency job instead tests a fresh
  latest-within-caps wheel installation.
- Both of those lanes run the core subset with `HAUTE_POLARS_UNPINNED=1`,
  declaring that they resolved Polars away from the lockfile on purpose. That
  selects the cap-range half of the Polars I/O interface contract — every
  registry callable still present, positional parameters unmoved — over exact
  equality with the single version `src/haute/_polars_io_arguments.json`
  records. Without the declaration a Polars whose version differs from the
  recorded one is an un-regenerated lockfile bump and fails, so the
  regenerate-on-bump gate still binds on every pinned lane; the skip is gated
  on the versions actually differing, so a declared lane that resolves the
  recorded version still runs the full comparison. The unlocked lane
  additionally prints `scripts/extract_polars_io.py --diff` to the run summary
  as a `continue-on-error` step: a stale schema is a regeneration prompt, not a
  lane failure, because the registry intersects the committed schema with the
  installed signature. That report names changed defaults individually, since a
  default is what an argument does when nobody sets it, and counts differences
  of kind, position or annotation, which no caller can observe.
- Mutation shards partition a shared initial work order and run mutants one at
  a time per runner. Separate CI runners provide the shard parallelism while
  serial execution avoids concurrent in-place mutation races; merge expects
  every selected mutant to contribute exactly once.
- A component roadmap contains active/deferred/decision/reverify work only.
  Delivered packages are removed once present-tense specs and ordinary tests
  own their outcome. Priority rows and package headings have identical unique
  package-id multisets; the index's `Start with` cell names an existing
  non-deferred package, or `—` when no such package exists.
- Generated contract outputs are byte-stable and committed. Check modes never
  rewrite or bless drift; missing, stale, nondeterministic, or unexpectedly shaped
  generator output is a hard failure. Standalone validators contain their runtime
  support and may not import Ajv from the production dependency graph.
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
- The toast store and the branch-loader single-flight are reset globally
  before every frontend test (a `beforeEach` in `frontend/src/setupTests.ts`);
  test files must not add their own hook-level copies of those resets.
  Deliberate toast seeding belongs inside the test or a file-level
  `beforeEach` (which runs after the global hook); the toast system's own unit
  tests keep explicit resets as part of their self-contained baseline. The
  global loader reset is safe because every settle path in
  `gitBranchLoader.ts` is identity-guarded — a detached request neither
  publishes state nor clobbers a newer request's slot.
- `tests/`, `frontend/src/__tests__/`, and the colocated frontend test
  directories are active corpora. `specs/roadmap/`, `repro/`, and generated
  output/reports must not be read as exhaustive or current behaviour merely
  because they remain tracked or present locally.
- Every component roadmap has `Scope`, `Priorities`, and `Planned improvements`
  sections. Every package supplies `Why`, `Plan`, `Acceptance`, `Dependencies`,
  and `Evidence`; a package appears in exactly one owning component.
- Dated supporting reports are explicitly listed separately from active component
  roadmaps in `tests/test_docs_accuracy.py`. The exact file inventory and local
  link checks include both categories. Reports link from the roadmap index and
  contain no package headings; only component queues have package lifecycle rules.
  The enumeration changes in the same commit that adds or retires a report.
- The normal Ruff configuration excludes `rating/`, `modules/`, `outputs/`, and
  generated pytest basetemp families (`.codex-pytest-*`, `.ops-pytest-temp`,
  `.providers-pytest-temp`, `.pytest-tmp*`). The former are lint-target
  boundaries; the latter may contain intentionally unreadable test fixtures and
  are never source inputs.
- The workflow coverage ledger must name every `specs/` component directory in
  at least one workflow, every node type exactly once, and every supplemental
  document from `specs/corpus.toml` in at least one workflow's `documents`;
  every document and contract path must be repository-relative, free of
  traversal, and an existing Markdown file under `specs/`; a contract's file
  must be one of its workflow's documents and its anchor must equal the
  GitHub-style slug of a heading in that file. A `gap` or
  `decision` scenario must name a package heading that is currently active in
  `specs/roadmap/`; a `not-applicable` scenario must give a reason; a `covered`
  scenario must reference at least one test and carry evidence with a commit
  hash, command, and result, each omission reported separately. A Python test
  reference must be a `test_*.py` file under `tests/` naming a `test*` function
  (inside a `Test*` class when qualified); a frontend reference must match the
  Vitest include globs or the Playwright `e2e/**/*.spec.ts` pattern and equal the
  complete first argument, one string literal (never an interpolated template)
  compared by its decoded runtime value, with comments allowed around it, of an
  `it()` or `test()` call, optionally
  through the property
  modifiers only, skip, fixme, fail, fails, todo, concurrent, sequential, and
  serial, or the called factories each, for, skipIf, runIf, and extend whose
  argument list is consumed before the title-bearing call (never a `describe`
  suite, a hook, or a fixture-only `extend`, and never inside a comment). The
  validator reuses the test-debt scanning
  primitives from `tests/_test_debt_scanner.py`: a Python reference whose
  module, class, or function carries a skip or expected-failure mark, and a
  frontend file containing any skip, fixme, fail, todo, or focus site, fail
  closed. The validator establishes existence and scheduling under static
  discovery rules, not assertion quality; a green result recorded at another
  snapshot is a review finding, not a validation failure.

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
- `scripts/check_critical_coverage.py` reports missing/under-threshold entries and
  `scripts/check_changed_coverage.py` reports exact missing changed lines/arcs;
  frontend `check-critical-coverage.mjs` does the corresponding validation for
  the coverage summary. These are explicit gate commands after test execution.
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

## Assistant evaluation lane

`tests/assistant_eval/support_matrix.json` is the closed, versioned threshold
contract. Held-out fixtures under `tests/assistant_eval/held_out/` are excluded
from package resources and checked against teaching-example IDs. The
task IDs in every matrix entry must exactly cover the scenarios supplied to
the runner: a missing threshold or an unexpected trial is a qualification
failure, so adding a scenario cannot silently leave it outside the release
gate. The
credentialed lane writes one JSON trial record per run plus an aggregate report
containing cold/warm p50 and p95 latency, tool/token/cost bounds, semantic task
rates, and safety counts. Missing trials, attribution drift, unauthorized
mutation, or leakage leaves a configuration unqualified.
`scripts/run_assistant_evaluation.py` is the fail-closed command boundary: it
loads one matrix configuration, held-out scenarios, and an explicit
`module:attribute` async live runner; executes the configured repetitions;
writes one atomic content-redacted v1 report; and exits non-zero unless every
live threshold passes. Canary values are counted for zero-tolerance scoring but
are never retained in the report artifact.

## Testing

- `tests/test_api_contract_generation.py` — deterministic Pydantic-to-schema generation, stale-check, schema-ownership, browser-safe-bound, and pinned-tooling contracts.
- `frontend/scripts/generate-api-contracts.test.mjs` — isolated schema-to-browser generation, standalone-validator import, per-artifact stale rejection, and read-only check-mode contracts.
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
- `tests/test_workflow_coverage.py` — workflow coverage ledger validator: the
  real ledger is valid and complete, plus malformed cases (duplicate ids,
  unknown component, missing owning package, each covered-state omission and
  malformed evidence field reported exactly, unresolvable, helper, production,
  traversal, absolute, out-of-root, skipped, or focused references, contract
  paths that are empty, directories, traversal, missing, or missing their
  heading, not-applicable without reason, undeclared supplemental documents,
  unmapped component or node type).

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
  reload, exact named API-input `sourceHandle`, immediate drill and render of an
  API Input inside a newly created unsaved submodel, and an Edge Join retained
  and highlighted in a downstream trace. Private React state is not an oracle.
- `tests/test_check_critical_coverage.py`, `tests/test_check_changed_coverage.py`,
  `tests/test_mutation_suite_runner.py`, `tests/test_run_mutation_pytest.py`,
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
  references and backend-test indexing, declared supplemental headings, link
  anchors, roadmap package bijection/evidence, present-tense ownership claims,
  unresolved temporary-contract tracking, positive-evidence retirement, and
  the one-line ratchet. `tests/test_spec_corpus_inventory.py` separately proves
  literal-set equality and fail-loud manifest validation.
- `tests/test_dependency_audit.py` covers clean/blocking reports, npm
  high/critical and transitive identities, valid/expired/malformed/duplicate/
  unused acceptances, the invariant that an accepted parent meta-finding cannot
  waive a child's concrete GHSA, malformed fail-closed reports, and live-command
  return-code orchestration without contacting advisory services.
- `mutation/` is tested as configuration/orchestration through its active
  script/tests and CI workflow. `specs/roadmap/`, `repro/`, and generated
  artifacts are intentionally not claimed as a current test suite.
