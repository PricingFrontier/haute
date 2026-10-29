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
| `.github/workflows/ci.yml` | Defines PR/main CI jobs: canary, install/package smoke, dependency floors, static/type checks, duration-balanced coverage shards, each recording its write-sandbox census, and their gate (which runs whatever the shards' result and fails explicitly when one did not pass, and fails when the census grows), duration-balanced compatibility shards, the process-mode route slice (`backend-process-mode`), the 3.14 probe, performance, the core-only and extras-installed optional-dependency lanes, platform, the strict documentation build (`docs-build`), mutation-config, frontend static and test lanes, and two browser E2E shards. Branch protection, outside this repository workflow file, determines which checks are required for merge. |
| `.github/workflows/dependencies.yml` | Runs the weekly/manual fresh unlocked-resolve monitor plus the locked Python/frontend advisory gate, the latter daily as well as on manual dispatch, main lock-policy changes, and relevant PRs; retains failed reports and raises/updates dependency-watch issues (the unlocked lane on a scheduled failure or a dispatch with `force_fail`, as the other monitoring lanes do; the audit on every non-pull-request failure), and closes a lane's issue when that lane next passes on `main` (`locked-advisory-all-clear` for the audit), since both lanes are deterministic for a given index or advisory feed. |
| `.github/workflows/frontend-shuffle.yml` | Runs the scheduled/manual shuffled Vitest monitor and raises/updates shuffle-watch issues with its seed on eligible failures. A green night never closes one: one passing seed does not prove an order dependence gone, so the fix's author closes it. |
| `.github/workflows/property-exploration.yml` | Runs the weekly/manual wide-budget Hypothesis lane (HAUTE_PROPERTY_EXAMPLES over `scripts/property_test_files.txt`, with the warnings plugin loaded so a RuntimeWarning fails it) and raises/updates property-watch issues on eligible failures. A green week never closes one: the fix pins the counterexample as an `@example`, and its author closes it. |
| `.github/workflows/container-smoke.yml` | Runs the weekly/manual container deployment smoke lane (process smoke check, Docker image build, run, /health and /quote verification) and raises/updates container-watch issues on eligible failures, closing the issue when the lane next passes on `main`. |
| `.github/workflows/e2e-snapshots.yml` | Manual-dispatch lane that re-renders Playwright screenshot baselines in the same Ubuntu environment the browser E2E lane compares them in, for the per-platform `-linux` baselines a Windows or macOS developer cannot produce. It writes nothing to the repository: it uploads only the baselines whose pixels changed, for review and commit alongside the change that moved them. |
| `.github/workflows/mutation.yml` | Plans changed mutation targets, runs separate CI-job shards whose mutants execute serially per runner, and uses a failure-aware non-cancelled status condition on the single merge gate so plan/shard failures become failed rather than skipped checks. |
| `.github/workflows/performance.yml` | Runs scheduled/manual Python and browser-performance lanes and uploads their artifacts. |
| `.github/workflows/write-sandbox-survey.yml` | Runs the monthly/manual write-sandbox survey: the suite with the sandbox forced strict, then `scripts/write_sandbox_promotions.py` over its JUnit report. Red while a test module passes whole under strict but still runs in observe mode; raises/updates a `sandbox-watch` issue on a scheduled or forced failure and closes it when the lane next passes on `main`. |
| `.github/dependabot.yml` | Has Dependabot propose the next commit, tag comment included, when an action a workflow uses releases. It covers GitHub Actions only: the lockfiles hold the Python and npm versions, and the dependencies workflow resolves them fresh every week. |
| `frontend/package.json` | Cross-component dependency owned by [build-and-distribution](../build-and-distribution/low-level.md); defines frontend lint/type/unit/coverage/bundle/E2E/benchmark command entry points and frontend critical-coverage entries. |
| `frontend/scripts/generate-api-contracts.mjs` | Pinned schema-to-browser generator: selects each contract's exact transitive definition closure, compiles every contract root into one TypeScript declaration module (property-level titles dropped, so a nested definition is declared once and an unrelated addition never renames an alias; objects are closed; array length bounds stay in the validators rather than becoming tuple types; a schema that says nothing of its type, pydantic's `Any`, is declared `unknown` through `untypedAsUnknown` rather than an object), emits constants, and bundles self-contained Ajv standalone ESM validator modules, one per pilot, one per converted response module group with one validator export per response and one per request group with one per request body (a group compiles one schema holding every definition its responses reach, so a shared definition compiles once), so Explore validation remains lazy. The eager execution validator co-exports its schema-version literal while Explore-only option constants remain in the lazy contract module. Its check mode compares every output byte without writing. |
| `frontend/scripts/generate-api-contracts.test.mjs` | Isolated generator contract: regenerates outside the repository dependency tree, imports both standalone validators without runtime Ajv, and proves that independently staling every generated output makes check mode fail without modifying the file. |
| `frontend/src/generated/api-contracts.schema.json`, `frontend/src/generated/api-contracts.generated.ts`, `frontend/src/generated/api-contracts.constants.generated.ts`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.mjs`, `frontend/src/generated/api-contracts.execution-strategy-diagnostic.validators.d.mts`, `frontend/src/generated/api-contracts.explore-charts.validators.mjs`, `frontend/src/generated/api-contracts.explore-charts.validators.d.mts`, and one `frontend/src/generated/api-contracts.<group>.validators.mjs`/`.d.mts` pair per converted response module group | Committed generated contract bundle, static declarations, lazy Explore constants, and split standalone runtime validators; the execution validator and declaration also export its schema version. They are reviewed build inputs, never edited by hand or regenerated at application runtime. |
| `frontend/eslint.config.js` | Defines blocking browser TypeScript/React ESLint rules (including `no-restricted-syntax` bans outside tests: `for (;;)`, `while (true)` and `setInterval`, which keep job polling in `hooks/jobPollingController.ts`, and local copies of the error-text, byte and duration formatting, and object-guard helpers, each exempt only in its owning module), the explicit pre-existing file/rule exceptions it lists, generated-report ignores, and underscore-prefixed intentionally-unused names. |
| `frontend/vitest.config.ts` | Configures the Vitest unit-test environment, setup, source/test selection, coverage reporting, and blocking 80/75/80/80 global thresholds. |
| `frontend/playwright.config.ts` | Configures serial browser E2E projects, retries, artifacts, and readiness-managed local E2E server. |
| `frontend/e2e/browserInteractions.ts` | Shared Playwright helpers for app-level modifier shortcuts, React Flow submodel double-click dispatch, and waiting for the canvas viewport to settle after the initial fit or a reveal glide. |
| `frontend/e2e/core-flows.spec.ts` | Playwright coverage for core browser flows. |
| `frontend/e2e/modelScoring.ts` | Shared Playwright helpers that add `novel_rows` (channels no model saw, and missing ones) and file-sourced Model Scoring nodes to the e2e pipeline and read a preview's prediction column; each modelling journey in `core-flows.spec.ts` scores the model file it saves through them. |
| `frontend/e2e/model-scoring.spec.ts` | Playwright coverage for Model Scoring beyond the modelling journeys: a classifier's positive-class probability column, a node set to the wrong task and its one-click fix, and a missing feature named by column planning, scoring models `scripts/e2e_scoring_models.py` trains beforehand. |
| `frontend/e2e/graph-editing-sequence.spec.ts` | Playwright witness, against the real backend, that a graph editing sequence (group and dissolve, disconnect, undo and redo, copy and paste, delete) conserves graph structure and previewed rows through save and reopen. |
| `frontend/e2e/rename-execution.spec.ts` | Playwright witness, against the real backend, that renaming an upstream node keeps a downstream coded transform executable through preview, save and reload. |
| `frontend/e2e/canvas-assurance.spec.ts` | Deterministic Chromium coverage and visual baselines for the loaded graph fitting inside the canvas, mixed Banding-to-Rating persistence, optimiser result/apply/MLflow-boundary journeys with a walk of every result pane, and a ratebook solve's Rates chart. |
| `frontend/e2e/data-io-nodes.spec.ts` | Playwright coverage for data-I/O node browser flows. |
| `frontend/e2e/edge-join.spec.ts` | Deterministic full-browser Edge Join workflow: compatible-edge feedback and insertion, configuration/preview, save/reload topology, repeated joins, named API-input source-handle preservation, immediate unsaved-submodel drill/render identity coverage, and downstream trace highlighting. |
| `frontend/e2e/active-node-reveal.spec.ts` | Deterministic Chromium witness that the active node stays visible beside its inspector and preview pane: a clicked node panned into the canvas corner is moved inside the narrowed canvas at unchanged zoom, and node search centres its node in that canvas at zoom 0.8. |
| `frontend/e2e/box-selection.spec.ts` | Chromium witness that a box selection ends once the editor changes the selection: after a box select, a node dropped from the palette follows its first drag and opens its own context menu on the first right-click. |
| `frontend/e2e/data-preview-scroll.spec.ts` | Chromium witness that a data preview keeps its scroll place: a preview scrolled fully right with the wheel stays fully right, with the new column in view, through two Refreshes that each add a column. |
| `frontend/e2e/data-preview-scroll.benchmark.spec.ts` | `@benchmark` Playwright coverage for data-preview scrolling. |
| `frontend/e2e/pipeline-settings.spec.ts` | Chromium witness that the Pipeline settings pane scrolls down to its last cached-data row in a short window: the inventory is never squeezed and clipped beneath the settings above it. |
| `frontend/e2e/git-graph.spec.ts` | Playwright coverage for the Git graph. |
| `frontend/e2e/git-sidebar-regression.spec.ts` | Playwright regression coverage for the Git sidebar. |
| `frontend/e2e/gitJourneys.ts` | Shared steps for the git journeys: renaming the e2e pipeline's `priced` node, saving until the save is on the branch's history, committing, creating and switching branches in the branch manager, and reading the pipeline file on disk and as a branch's latest save holds it. |
| `frontend/e2e/git-restore.spec.ts` | Restoring a version and starting a branch from one, with the mistakes on the way: unsaved edits at the prompt (save first, discard, cancel), Cancel, Escape and a click outside, a double click, restoring the wrong version, no restore on the current version or another branch, a milestone after a restore, and branch names git cannot use, protected, taken or ending in `-save`, said in the name box. |
| `frontend/e2e/git-branches.spec.ts` | Switching, deleting and restoring branches: the canvas following a clean switch and back, unsaved edits at a switch (cancel, discard and Undo, save first), a branch the open pipeline is not on (the failure view, Save disabled, the Git panel beside it to switch back, also after a reload there), Deleted and Restore, a deleted name reused (Restore as, the taken name refused), Undo of a delete, deleting the branch you are on, and archiving it with unsaved edits. |
| `frontend/e2e/git-recovery.spec.ts` | Getting back on track after git use outside Haute: a project left on an earlier version (the chooser keeping it on a new branch, choosing the existing branch instead, closing the chooser, saving before choosing, an unusable name), a switch to another branch (Go back, Open the branch manager), files a git action left out of step (Files in use, Retry, Save and Commit refused), and, on Windows only, a switch over a file another process holds open, shared for reading or not at all. |
| `frontend/e2e/save-conflict.spec.ts` | Two-page Playwright witness for the Save base-revision conflict: with live sync cut off, a stale save is rejected while the local edit survives, and a fresh save succeeds after an explicit reload. |
| `frontend/e2e/rebuilt-frontend.spec.ts` | Chromium witness for a page served before a rebuild: with a lazily loaded panel's chunk request aborted after page load, the panel offers "Haute has been updated" and a Reload (no dead "Try again"), and the reload loads the panel. |
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
| `scripts/collect_changed_snapshots.py` | Stages the screenshot baselines a Linux render changed, for the snapshot-refresh lane's artifact. Enumerates with `--untracked-files=all` so a wholly new snapshot directory is reported file by file rather than as one entry, and lets a failed Git query raise rather than read as "nothing changed". |
| `scripts/check_critical_coverage.py` | Enforces configured backend per-file statement/branch coverage floors from coverage JSON. |
| `scripts/check_changed_coverage.py` | Intersects a Git new-file-line diff with Coverage.py format-3 statement and branch-arc evidence for every changed Python file under `report_paths`: enforces 100% changed statement and branch coverage for the safety-critical `paths`, and reports the other files on standard output and in the GitHub job summary without failing. |
| `scripts/check_dependency_audit.py` | Stdlib-only fail-closed advisory-policy orchestrator/parser: exports the exact locked Python graph, runs pinned `pip-audit` and full-tree `npm audit`, validates report schemas, gives npm meta-findings a topology-independent transitive identity, and subtracts only current exact accepted-risk entries. |
| `security/accepted-risks.toml` | Versioned exact advisory acceptance registry. Entries require ecosystem/package/advisory identity, owner, exposure, compensating control, approval date, and non-expired review date; stale, duplicate, malformed, mismatched, or unused entries fail the audit. |
| `scripts/core_test_files.txt` | Curated core test-subset manifest and its selection/refresh rationale for canary/dependency lanes. |
| `scripts/property_test_files.txt` | Manifest of every Hypothesis test module for the property-exploration lane; `tests/test_workflow_coverage.py` keeps it equal to the modules that import `hypothesis`. |
| `scripts/test_file_durations.json` | Recorded per-module backend test durations, one table per sharded lane (`coverage`, `compat`), used only to balance the CI shards (`tests/_ci_shards.py`). |
| `scripts/refresh_test_durations.py` | Rewrites one lane's table (`--lane`) in `scripts/test_file_durations.json` from pytest JUnit reports (xunit1 family), summing each module's test-case times and keeping the other lanes' tables; with `--median-of-runs` each report path is one run, and each module keeps its median across the runs that recorded it; refuses reports without test cases or with a test case lacking its `file` or a finite non-negative `time`, and an existing file it cannot read as lane tables. |
| `scripts/e2e_git_topologies.py` | Exercises Git topology scenarios used by repository-level verification. |
| `scripts/e2e_scoring_models.py` | Trains small GLM, XGBoost and CatBoost-classifier model files into the e2e project, through the editor's training path, for `frontend/e2e/model-scoring.spec.ts`. |
| `scripts/generate_api_contracts.py` | Deterministically composes the execution-strategy diagnostic and Explore chart root models (validation mode), the request models of each request group (`REQUEST_CONTRACT_GROUPS`, validation mode, as a route reads a body, listed under `x-haute-request-groups`) and the converted response models of each module group (`RESPONSE_CONTRACT_GROUPS`, serialization mode with every sent field required) into one Draft 2020-12 JSON Schema bundle (a serialization-mode definition that differs from a pilot's validation-mode one of the same name is emitted as `<Name>Output`, with its references rewritten; a response root is never renamed), fixes the recursive finite-JSON definition, drops OpenAPI's `discriminator` keyword from a tagged union (its `oneOf` branches carry the tag as a `const`; Ajv in strict mode refuses the keyword), and atomically writes or byte-checks the committed frontend source artifact. |
| `scripts/extract_polars_io.py` | Extracts the Polars I/O argument schema by introspection, and with `--diff` reports installed-versus-committed drift as a non-failing Markdown freshness summary. |
| `scripts/init_smoke.py` | Builds/installs or consumes a wheel in a fresh environment, initialises a project, serves it headlessly, exercises an authenticated endpoint, and shuts it down; `kill_process_tree` fells a launcher and its children (`taskkill /T` on Windows), which the browser e2e server reuses. |
| `scripts/memory_smoke.py` | Runs the maintained memory-safety smoke path. |
| `scripts/package_smoke_check.py` | Validates an installed distribution's package/runtime expectations, each model engine's installed version against the range the wheel's own requirements declare for the platform (`_assert_within_declared_range`), so a cap bump needs no edit here. |
| `scripts/update_assistant_example_manifests.py` | Checks or explicitly refreshes closed content-addressed assistant example inventories; unsafe, duplicate, missing, and undeclared paths fail in both modes. |
| `scripts/update_assistant_prompt_golden.py` | Cross-component dependency owned by [assistant](../assistant/low-level.md); checks, or with `--write` refreshes, the golden snapshot of the assistant's system prompt and provider tool schemas, failing with a unified diff per changed file. |
| `scripts/preflight.ps1` | Windows preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/check_write_sandbox_census.py` | The coverage gate's census check: reads every coverage shard's write-sandbox census, classes each write by the deepest recorded root that holds it, sets the harness's own writes apart, and fails on a class missing from, or a class lingering in, the census table of `tests/write_sandbox.toml`; a missing or malformed shard record fails too. Writes a class table to the job summary. |
| `scripts/write_sandbox_promotions.py` | The write-sandbox survey's verdict: from the strict run's JUnit reports, names each test module that ran a test and passed every one, and is neither strict already nor allowlisted by the lint, failing while any remain. |
| `scripts/lint_pins.py` | Enforces the asymmetric dependency-pinning doctrine against the declaration layer: published dependencies need an explicit floor and cap and may not be exact-pinned or prefix-matched, tooling dependencies must be exact with a concrete version, executable surfaces may not invoke `npx`, and every action a workflow or composite action uses names the 40-hex commit it runs, with its tag in a comment (local actions and container images aside). |
| `scripts/preflight.sh` | POSIX preflight entry point for selected backend/frontend/init-smoke checks. |
| `scripts/regen_sanitize_parity_fixture.py` | Regenerates the retained backend compatibility golden when deliberately requested. |
| `scripts/report_flaky_browser_tests.py` | Reads Playwright's JSON report and names each browser test that passed only on a retry, in the job summary and as a warning annotation; a missing report fails rather than reporting nothing. |
| `scripts/run_frontend_e2e_server.py` | Generates the isolated browser fixture, then starts and readiness-signals its dedicated-port backend and Vite proxy for Playwright; on Windows it stops each with `init_smoke.kill_process_tree`, since the npm launcher's Node child outlives a plain terminate on Vite's port. |
| `scripts/run_mutation_pytest.py` | Runs a mutation witness command from a fresh synthetic project while retaining repository pytest configuration and placing pytest inputs in a sibling temporary boundary, so relative Haute runtime state cannot leak between mutants or alter path-confinement semantics. |
| `scripts/run_mutation_suite.py` | Implements mutation target selection, work planning, shard execution, merge, and survival-threshold reporting. |
| `scripts/run_perf_suite.py` | Runs bounded Python performance tests and writes schema-4 workload, environment, resource, wall-time, and per-test evidence artifacts. |
| `scripts/spec_corpus_inventory.py` | Recursively classifies every supported-suffix specification file, rejects undeclared nested documents, builds the exact working-tree content fingerprint, validates complete per-file review coverage, and derives component/supplemental/governance/roadmap totals. |
| `scripts/setup-worktree.sh` | Sets up a development worktree. |
| `mutation/README.md` | Documents the maintained mutation-testing workflow and constraints. |
| `mutation/targets.json` | Declares selected mutation targets, witness suites, survival budgets, and rationales. |
| `mutation/cosmic-ray.jsonpath.toml` | Cosmic Ray configuration for JSONPath mutation coverage. |
| `mutation/cosmic-ray.json-shred.toml` | Cosmic Ray configuration for JSON-shredding mutation coverage. |
| `mutation/cosmic-ray.output-assembler.toml` | Cosmic Ray configuration for output-assembler mutation coverage. |
| `tests/` | Active Python unit, integration, property, regression, contract, E2E-support, and repository-hygiene test corpus. |
| `tests/test_assistant_example_portfolio.py` | Ordinary specialist evidence for the packaged assistant portfolio: source parity, trace/dry-run, real training/scoring and optimisation/apply, deployment preflight, and adversarial rejection. |
| `tests/test_api_contract_generation.py` | Backend contract-generation evidence: deterministic Pydantic-to-schema output, stale/read-only check behaviour, root/definition ownership, request groups read as their routes read a body (validation mode, a defaulted field optional), recursive finite JSON, safe-integer bounds, collection caps, and pinned frontend code-generation dependencies. |
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
| `tests/write_sandbox.toml` | The test write-sandbox ratchet's state: the modules that run strict, the lint allowlist's site fingerprints per unconverted file, and each class of out-of-sandbox write the observe-mode census may still record, with its reason. |
| `tests/workflow_coverage.toml` | Versioned workflow coverage ledger: workflow families, node types, and scenario records with coverage state, owning package, test references, and execution evidence. |
| `tests/test_workflow_coverage.py` | Ledger validator (`load_ledger`, `roadmap_package_ids`, `ledger_violations`) and its malformed-ledger cases; a meta-marked repository-health module. |
| `tests/_ci_shards.py` | Pytest file sharding for the CI full-suite lanes, wired by `tests/conftest.py`: `--shard=K/N` keeps only the test modules assigned to shard K and ignores every other module before it is imported, balanced on the durations table `--shard-lane` names, and runs the shard's longest modules first, each module's tests in order (an unrecorded module counts as the table's median); `--shard-durations` names an alternative durations file. |
| `tests/_test_debt_scanner.py` | Test-debt scanning primitives (backend AST visitor, frontend source scanner, frontend test-file predicate) shared by `tests/test_test_debt.py` and `tests/test_workflow_coverage.py`; a support module, not a test. |
| `tests/_property_budget.py` | Shared Hypothesis settings for the generated families: `pr_budget` (small, derandomised, no example database, reproduction blob printed) and the `exploration_examples` override read from the `EXPLORATION_ENV` variable (HAUTE_PROPERTY_EXAMPLES); a support module, not a test. |
| `scripts/benchmarks/` | Point-in-time benchmark, probe and reproduction programs with their raw results and metadata, linked from the dated reports under `specs/roadmap/`; not an automatically current product-behaviour contract. `specs/roadmap/` itself holds only Markdown reports and the provenance record its index links. |

## Key types and data structures

- **Backend critical-coverage entry** in `pyproject.toml` has a source `path`,
  minimum statement/branch percentages, and rationale. The coverage JSON path
  is `.cache/coverage/backend.json`.
- **Changed-code coverage configuration** in `pyproject.toml` names the exact
  safety-critical source files the gate governs (`paths`), the directories
  whose other changed Python files are reported only (`report_paths`), and
  fixes both changed statement and changed branch coverage at 100%. Both lists
  are required. A branch arc is in scope when either positive source-code
  endpoint is a changed new-file line. The checker unions the
  merge-base-to-HEAD diff, current tracked worktree changes, and untracked
  source files in scope so local and clean-CI runs apply the same
  executable-line contract. A gated file missing from the coverage artifact
  fails; a reported file Coverage.py does not measure is left out.
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
  failure, and writes a JSON report (`test-results/report.json`) beside the list
  and HTML ones. `failOnFlakyTests` holds when `HAUTE_E2E_FAIL_ON_FLAKY=1`, which
  the CI workflow sets on a push to `main` only, so there a test that passed
  only on a retry fails the run; on a pull request
  `scripts/report_flaky_browser_tests.py` names each such test in the job
  summary instead.
- **Interactive execution mode in tests.** Production runs previews, traces,
  free-code step resolution and the optimiser estimate in warm worker
  processes (`process`, the default). The autouse fixture
  `tests/conftest.py::_interactive_execution_test_mode` runs them in threads for
  every test, because most tests patch in-process seams a worker would not
  see; the isolation tests opt back into processes themselves.
  `HAUTE_TEST_INTERACTIVE_EXECUTION_MODE=process` keeps production's mode for a
  whole run, with one warm worker per test process, shut down after each test
  so each test starts it with its own environment. The `backend-process-mode`
  CI lane runs the route slice that way: `tests/test_interactive_route_isolation.py`,
  `tests/test_server.py`, `tests/test_trace_api.py`,
  `tests/test_trace_multi_frame.py`, `tests/test_adversarial_inputs.py` and
  `tests/test_security_gaps.py`. A test marked `in_process` patches a seam and
  stays in threads even there. An interactive worker resolves the project root
  from its own working directory, so a test that sets the root in memory
  without changing directory fails across the boundary for that reason;
  carrying the project into every worker request is `PCFG-R04`'s.
- **E2E project reset** (`frontend/e2e/projectIsolation.ts`) verifies the git toplevel,
  restores `main`, deletes other branches, version tags and the deleted branches' pins under
  `refs/haute/`, scrubs untracked files with `git clean -fdx`, and reseeds the working
  branch. `e2eGit` runs one git command in the e2e project after the same toplevel check,
  for a spec that does what an analyst does in a terminal or reads back what the app did. A test can finish while the backend
  still completes work that test started — an input-snapshot build holds its store lock
  files open for its duration — and Windows cannot delete an open file, so the scrub is retried every
  250 ms until it succeeds, for at most 30 seconds; after that the reset fails with git's
  error as the cause rather than starting the next test on a dirty project.
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
  `slow`, `perf`, `sandbox_strict`, `meta`, and `in_process` markers
  (meta-marked repository-health checks, which every test scanning the
  repository's files must be, are deselected from the Python compatibility
  lanes in `.github/workflows/ci.yml`).
- **Test-file durations** in `scripts/test_file_durations.json` are
  `{"version": 2, "lanes": {"<lane>": {"tests/<module>.py": seconds}}}`: one
  table per sharded lane, because coverage tracing slows some modules far more
  than others, of repository-relative POSIX module paths and finite non-negative
  seconds rounded to one decimal place. `coverage` is measured on the 3.12
  coverage lane and `compat` on the 3.13 compatibility shards. **Shard
  assignment** for `--shard=K/N --shard-lane=L` sorts the modules lane L
  records that still exist by descending duration (ties by path) and places
  each on the least-loaded shard (ties to the lower index); any other test
  module goes to shard `crc32(path) mod N + 1`. The assignment depends only on
  N, the lane's table, and the files present, so every shard and every xdist
  worker computes the same partition.
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
  and result, the command keeping pytest's warnings plugin loaded (no
  `-p no:warnings`); a `not-applicable` scenario carries a reason; `gap` and
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
   CI runs Ruff, mypy over `src/haute/` and `scripts/` (the CI gates; the
   point-in-time `scripts/benchmarks/` excepted), and `HAUTE_BUILD_FRONTEND=1 uv build`.
3. Backend coverage runs the full test corpus on 3.12 in four file shards
   (`--shard=K/4 --shard-lane=coverage`). Each shard uploads its coverage data file and a JUnit
   report, the input to `scripts/refresh_test_durations.py`, whether its tests
   passed or not. The gate runs whenever the shards ran (`!cancelled()`); when
   any shard did not pass, its first step fails explicitly and the later steps
   still run, so the floors are reported rather than skipped. It combines
   the four coverage files, enforces the global 90% floor, writes JSON,
   invokes `scripts/check_critical_coverage.py` for per-file floors, then invokes
   `scripts/check_changed_coverage.py` against the pull-request base SHA (or the
   preceding main revision for a push). The coverage-gate checkout contains the
   required history; an unreadable base revision is a gate failure rather than an
   empty-diff pass.
   Both gates read the same safety-critical list (the high-level specification).
   A floor for a module added to the list is seeded two points below its
   measured statement and branch coverage from a green CI run; a module that is
   retired leaves both lists in the same change, since a deleted file cannot
   satisfy a coverage gate. The changed-code check also writes a table of every
   changed file with changed executable code, gated or reported, to the job
   summary.
4. Compatibility runs the same corpus without coverage, excluding `perf` and
   `meta` tests, in three file shards on each of 3.11 and 3.13
   (`--shard=K/3 --shard-lane=compat`), each uploading a JUnit report whatever
   its result, the input to the `compat` table's refresh. Optional-dependency, platform, package, init, and mutation
   configuration smoke lanes run their named commands. The core-only
   optional-dependency lane installs the project with the `test-harness`
   dependency group alone (pytest, pytest-asyncio, httpx and Hypothesis, which
   `tests/conftest.py` imports), never the dev group, whose `haute[databricks]`
   would restore the SQL connector; a step of its own asserts that core MLflow
   is present and the Databricks SQL connector absent, then the smoke module runs
   through `uv run --no-sync`, since a bare `uv run` syncs the default groups
   back in. The module's own skip lets a full development environment pass over
   it; the lane cannot. The `docs-build` job runs `mkdocs build --strict`.
   The 3.14 probe is
   explicitly allowed to fail without blocking the workflow result.
5. Frontend CI runs two jobs after `npm ci`. The static job runs
   `npm run check:contracts`, `typecheck`, `lint`, `build`, `check:bundle`,
   and `test:benchmark:pr` as separate steps in that order; the tests job runs
   `npm run test:coverage` (Vitest with its global thresholds, then the
   critical-coverage check). Together they run exactly the npm scripts of the
   frontend-only preflight, which remains the local equivalent. Every
   frontend preflight mode runs `npm run check:contracts` before type checking;
   the backend core subset separately proves the Pydantic-to-schema stage through
   `tests/test_api_contract_generation.py`. Ordinary regeneration runs the Python
   stage followed by the Node stage, while application build and runtime only
   consume the committed results. Browser
   E2E runs as two Playwright shards (`npm run test:e2e -- --shard=K/2`, which
   Playwright splits by test count over whole spec files); each shard
   synchronises Python, installs Chromium/Firefox, starts its own server, and
   uploads its own report. Playwright calls `scripts/run_frontend_e2e_server.py` and
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
   modulepreload chunks against its default ceilings for initial and total
   JavaScript gzip (`DEFAULT_MAX_INITIAL_JS_GZIP_KIB` and
   `DEFAULT_MAX_TOTAL_JS_GZIP_KIB`), whose comments record the measured sizes
   each ceiling leaves headroom over; this specification states no figure of
   its own, so a raise is made in that file alone. Initial-path raises admit
   only deliberate eager core and retain roughly 2 KiB of headroom, whereas an
   intentional lazy feature restores roughly 10 KiB of aggregate headroom.
   The Explore chart validator is a separate lazy artifact in the chart-config
   chunk rather than an entry modulepreload.
   Modelling training response parsers remain in a dynamically imported
   `types/trainGuards.ts` chunk. The checker classifies that chunk as lazy-only
   and fails if it becomes a startup modulepreload; CI may override the ceiling
   only through the documented
   `HAUTE_BUNDLE_MAX_INITIAL_GZIP_KIB` environment variable.
   User-triggered surfaces such as the Ctrl+K `NodeSearch` palette remain
   dynamically imported so their implementation is excluded from that initial
   chunk. Preview input-cache preparation also loads on demand, and its
   `ensureInputSnapshots` chunk must not appear in startup modulepreloads.
   The completed-model `ModellingPreview` and its summary/chart views likewise
   load only when the selected node has model results.
   Canvas-assurance screenshots retain the shared 2% pixel-difference
   ceiling. The narrow mixed-Banding and rebuilt-Rating captures and both
   selected-optimiser captures select reviewed Linux-specific baselines in
   Linux CI; the two desktop Banding/Rating captures deliberately keep the
   default developer baseline. Platform-specific baselines are added only when
   repeated captures prove stable system-font or native-control differences.
6. Mutation CI calls `scripts/run_mutation_suite.py --phase plan`, executes
   each isolated target/shard, downloads all artifacts, and calls `--phase merge`
   to enforce total survivor budgets. Planning uses each target's required
   `max_pending_per_shard` cap: pending means executable mutants only, shard count
   is `max(1, ceil(pending / cap))`. Plans are split in stable order across a
   primary and optional overflow matrix, each respecting GitHub Actions'
   256-job limit; plans above the combined 512-job capacity fail explicitly.
   Every planned shard appears in exactly one matrix, and the merge gate
   requires both matrices to succeed when overflow is present. The two jobs
   share the same execution steps. The cap is calibrated to retain timeout and
   artifact-upload headroom; it must not be weakened by silently overpacking a
   target. The JSON shred target uses at most 20 mutants per shard against its
   90-second expanded witness ceiling, and the shard job has a 40-minute hard
   limit so the 30-minute worst-case test budget still leaves setup and artifact
   headroom. The merge job's `!cancelled()` status
   condition ensures dependency failures do not skip it, and it fails explicitly
   when planning or a required shard was unsuccessful. Plan artifacts are uploaded
   even after a baseline or planning failure so the failing test output remains
   available for diagnosis. Plan and merge artifacts
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
- A sharded backend lane partitions test modules, never individual tests: a
  module belongs to exactly one shard and every other shard ignores it before
  import, so the union of a lane's shards is exactly the unsharded collection.
  Recorded durations only balance the shards. A stale, missing, or renamed
  entry can unbalance them but can neither drop nor duplicate a test, so
  refreshing the durations file is maintenance, not a correctness step. The
  table is refreshed from several green runs' medians: a job's runner differs
  in speed from the next job's by around a tenth, so one run's table carries
  each shard's runner into the next balance, and one run's shard times show
  the runners as much as the balance. Balance is judged on each shard's median
  over several runs. Within a shard the table orders the run longest module
  first, so the long modules overlap instead of one finishing alone at the end
  while the other workers wait. A
  malformed `--shard` value (not `K/N` with 1 ≤ K ≤ N), `--shard` without
  `--shard-lane`, a lane the file has no table for, or a missing, unreadable,
  wrongly versioned, or malformed durations file fails the run before
  collection. Each sharded CI job's matrix lists exactly `1..N` for its
  `--shard=${{ matrix.shard }}/N`, and the coverage gate combines exactly the
  data files of shards `1..N`; a shard left out of the matrix would otherwise
  drop its modules silently.
- The two frontend CI jobs together run exactly the npm scripts that the
  frontend-only preflight runs, with the bundle budget after the build, so the
  split cannot lose a gate that the local preflight still runs.
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
- A pytest session owns an isolated source-cache coordination table and closes
  its process-owner handles at session teardown before restoring any previous
  table. This lets an embedded mutation run remove its temporary project on
  Windows after pytest returns, while production owner locks retain their
  process lifetime.
- The JSON-shred witness selection includes complete-inference cache isolation,
  concurrent request sharing and revision invalidation, native-filter equivalence
  to the full inference walk, bounded prefix learning, fused-parser error parity,
  and exact byte-range record limits. These contracts accompany the optimized
  inference implementation in the focused mutation command.
- Native-filter witnesses check that learned shapes actually use the fast path,
  including signed integer endpoints, scalar and object arrays, and the depth
  boundary; schema equality alone cannot detect a disabled optimization. Seeded
  filters retain the same compilation budget. Cache witnesses compare equal
  independently obtained revisions and exercise process changes in either
  direction, while scan-level source-change checks run independently of the cache.
- A retained skip, xfail, expected failure, focused test, flaky marker, or
  browser retry is debt even when it is justified. Exact-site fingerprints
  prevent silent growth: a new site fails the ratchet until it is explicitly
  reviewed and budgeted, and a removed site fails until its stale entry is
  deleted (ruled 2026-07-27: no calendar expiry — review is event-driven,
  triggered by the ratchet, not by dates).
- Frontend debt chains continue only through calls or explicit member access
  (`.`, `?.`, or `[...]`). Adjacent declarations without semicolons are separate
  expressions: a later skip/focus marker must not be attributed to earlier
  tests, and each earlier declaration must stop before scanning later bodies.
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
  directories are active corpora. `specs/roadmap/`, `scripts/benchmarks/`, and generated
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
- The normal Ruff configuration excludes `examples/`, `modules/`, `outputs/`, and
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
  hash, command, and result, each omission reported separately, and an
  evidence command that unloads the warnings plugin (`-p no:warnings`) is
  reported. A Python test
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

## Testing

- `tests/test_api_contract_generation.py` — deterministic Pydantic-to-schema generation, stale-check, schema-ownership, browser-safe-bound, and pinned-tooling contracts.
- `frontend/scripts/generate-api-contracts.test.mjs` — isolated schema-to-browser generation, standalone-validator import, per-artifact stale rejection, read-only check-mode contracts, and an untyped schema declared `unknown`, nested in a list, an array's items or a union.
- `tests/test_bug_regressions.py` — named deep-review bug regressions (streaming restoration, source/pipeline path handling, preview source files, and Polars/parser safety).
- `tests/test_bugfixes.py` — regression contracts for streaming chunk restoration, source/pipeline path resolution, preview source files, and parser/config safety.
- `tests/test_ci_shards.py` — `--shard` parsing and durations-file validation
  failures, each lane reading its own table and a lane without one refused,
  deterministic longest-first assignment with ties and CRC-32 placement of
  unrecorded modules, longest-module-first order within a shard (each module's
  tests in order, an unrecorded module at the median, an unsharded run
  untouched), a pytester partition proof (each module runs in exactly
  one shard, an unassigned module is never imported, and xdist workers select
  the same modules), `scripts/refresh_test_durations.py` aggregation into one
  lane with the others kept, each module's median across runs, and refusal
  cases (one run's reports holding a test twice among them), and the CI invariants that
  every sharded matrix lists `1..N` for its `--shard=${{ matrix.shard }}/N`,
  each sharded backend lane balances on a table the file records, the coverage
  gate combines exactly those shards' data files, and the refresh recipe
  downloads the JUnit artifacts the coverage and compatibility shards upload.
- `tests/test_coverage_gaps.py` — targeted edge coverage for config/fingerprint/builders/artifacts/model helpers, schemas, and node discovery.
- `tests/test_decoupling_contracts.py` — separation contracts for tracing, EventBus/file-watcher integration, and logging conventions.
- `tests/test_dry_fixes.py` — DRY response/model inheritance and optimiser finalize contracts across online/ratebook/frontier paths.
- `tests/test_dry_refactors.py` — shared exception hierarchy, dispatch-table parity, transport resolution, dead-code removal, typed-node lookup, and shared code compilation.
- `tests/test_frontend_e2e_server.py` — browser-harness contracts for augmenting the blank scaffold with a complete, executable fixture graph, and its process stop: the whole tree on Windows, `terminate()` elsewhere.
- `tests/test_meta_marker_coverage.py` — every test that scans the repository's files (walks it through `source_files`, or runs a full-corpus debt or write-sandbox scan, itself or through a helper in its module) carries the `meta` marker, so the compatibility lanes leave it to the coverage lane; a test that builds the tree it walks is not a scan, and `BEHAVIOURAL_WALKS` names each walk that belongs to the behaviour under test, an unneeded entry failing.
- `tests/test_package_smoke_check.py` — the package smoke's engine ranges come from the installed wheel's requirements: the installed engines are inside them, and a version past a cap or an undeclared distribution is refused.
- `tests/test_performance_docs.py` — documentation contracts for Python/Polars/frontend/memory performance workflows and links.
- `tests/test_property.py` — Hypothesis properties for sanitisation, topology, path resolution, banding/rating, codegen/parser round-trips, fingerprints, config, validation, and cache invariants.
- `tests/test_repository_hygiene.py` — repository artifact/path (no tracked `node_modules/` directory or tool cache anywhere in the tree), dependency-import, subprocess, encoding, sanitizer, and persistence-path hygiene.
- `tests/test_small_module_contracts.py` — JSON-safe serialization, shared contracts, and package-init module contracts.
- `tests/test_test_debt.py` — AST debt scanner budgets and explicit-reason
  contracts for backend/frontend skip/xfail/fixme markers, plus the zero
  backend-flaky budget, the Playwright retry budget, and exact regeneration of
  `tests/test-health-summary.md`.
- `tests/test_workflow_coverage.py` — workflow coverage ledger validator: the
  real ledger is valid and complete, plus malformed cases (duplicate ids,
  unknown component, missing owning package, each covered-state omission and
  malformed evidence field reported exactly, unresolvable, helper, production,
  traversal, absolute, out-of-root, skipped, or focused references, an
  evidence command that unloads the warnings plugin, contract
  paths that are empty, directories, traversal, missing, or missing their
  heading, not-applicable without reason, undeclared supplemental documents,
  unmapped component or node type, a witness under `tests/performance/` or
  in a module carrying a module-level perf mark, which the ordinary lane's
  `-m 'not perf'` addopts would deselect), and the property-manifest
  completeness check (`scripts/property_test_files.txt` equals the set of
  `tests/test_*.py` modules importing `hypothesis`).

- Generated families (ENG-T11) run under `tests/_property_budget.py::pr_budget`:
  a small explicit `max_examples`, `derandomize=True` (the seed derives from the
  test itself, so a failure reproduces regardless of test order), `database=None`
  (no dependence on a local `.hypothesis` cache) and `print_blob=True`; shrunk
  failing examples are retained as `@example`s, and every family carries a
  negative control built with `hypothesis.find` under the same settings. Setting
  `HAUTE_PROPERTY_EXAMPLES` lifts the budget and re-randomises the search; the
  weekly `property-exploration` workflow runs the manifest that way (it is not a
  PR check) and opens a `property-watch` issue on failure.
- Regression sensitivity of the fixed runtime findings (ENG-T12 step 3) was
  replayed on 2026-09-06 at commit `9daa4372` in a detached worktree: the ledger
  witnesses of each finding ran green at HEAD and then against the pre-fix
  version of the fixed source files (`git show <fix>^:<path>`), all going red:
  F9 stale saves (`src/haute/routes/_save_pipeline.py` and
  `src/haute/routes/pipeline.py` before `e512dc5f`: 10 of 10 witnesses fail),
  F10 watcher self-write suppression (`src/haute/routes/_helpers.py`,
  `src/haute/_file_ops.py` and `src/haute/server.py` before `e512dc5f`: 6 of 12
  fail), F12 preamble pinning (`src/haute/executor.py` and
  `src/haute/routes/_optimiser_service.py` before `00bbe3ff`: 4 of 13 fail), F3
  private child endpoints (`src/haute/_parser_conservation.py`,
  `src/haute/_parser_submodels.py` and `src/haute/parser.py` before `4f2b0865`:
  5 of 5 fail), F4 restart target (`src/haute/_project_storage.py` before
  `e46d9c6a`: 7 of 40 fail), F2 create-only publication
  (`src/haute/_uc_transport.py` and `src/haute/_storage_types.py` before
  `272af286`: all 9 error at the removed create fence), and F11 rename binding
  (`frontend/src/utils/nodeUpdatePlan.ts` before `a9f7779a`: 7 of the 57
  Vitest witnesses fail; the browser journey is not replayed). This is evidence for
  those snapshots only; it is not a per-PR gate.
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
- `tests/test_report_flaky_browser_tests.py` — flaky browser tests named with
  their describe path and project from a Playwright JSON report, in the job
  summary and as warnings; a run with none says so and claims nothing about
  tests that failed outright; a missing or malformed report fails; and the CI wiring (`failOnFlakyTests` on a push to `main`, the
  summary step after the browser run) is in place.
- Fixture git commands go through `tests/_git_helpers.py::git_run`, which
  raises at the call with git's stderr; `check=False` is only for a query whose
  non-zero exit is its answer. `tests/test_git_helpers.py` runs a real failing
  command both ways.
- `tests/test_test_isolation.py` — test orders that once leaked state into a
  later test on the same worker, found when the CI study ran the suite under
  `--dist loadfile` and `-n 8`: each case runs its pair in one child pytest, in
  that order, and requires both to pass. The package-version fallback test now
  restores `haute` after undoing its patch of `importlib.metadata.version`, and
  the host-binding module patches the port probe through the test's own
  `MonkeyPatch`, so a test that patches it again is undone in order.
- `tests/test_safety_critical_lists.py` — a `meta` check that the changed-code
  gate's paths, the critical floors and the high-level specification's list name
  the same modules, read as the two gates read `pyproject.toml`, and that a
  module added to any one list alone is named as missing from the other two.
- `tests/test_check_critical_coverage.py`, `tests/test_check_changed_coverage.py`,
  `tests/test_mutation_suite_runner.py`, `tests/test_run_mutation_pytest.py`,
  `tests/test_mutation_sharding.py`, `tests/test_run_perf_suite.py`,
  `tests/test_perf_suite_script.py`,
  `tests/test_memory_smoke_script.py`, `tests/test_frontend_bundle_budget_ci.py`,
  `tests/test_infrastructure_contracts.py`, `tests/test_docs_accuracy.py`, and
  `tests/test_spec_corpus_inventory.py`
  cover important assurance tooling and repository-policy contracts: among
  them, the frontend CI jobs run exactly the npm scripts of both preflight
  scripts (`preflight.sh` and `preflight.ps1`), and every monitoring lane raises
  its alarm only on a scheduled failure or a dispatch with `force_fail`. The
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
- `tests/test_lint_pins.py` covers the published floor/cap rule and its
  rejection of exact pins, prefix matching, compatible-release and direct
  references; tooling exactness as one `==` with a concrete version, and the
  bare project self-reference exemption against a self-reference carrying a
  specifier; npm exactness across the install sections and the deliberate
  `peerDependencies` exemption; lexical `npx` detection in shell grouping,
  line continuations and package scripts against its non-detection of shell
  comments, markdown prose and shim paths; commit pins for the actions that
  workflows, reusable workflows and composite actions use, refusing tags,
  branches, short commits and a commit without its tag comment, exempting
  local actions and container images, and not mistaking a mention in a
  comment or a `run:` line for a use; annotation line selection and
  workflow-command escaping; and both ratchet directions against the real
  manifests through the collector — the live tree stays clean, and removing a
  real Python cap, relaxing a real npm pin or moving a real action back to
  its tag is caught.
- `tests/test_write_sandbox_lint.py` covers the scanner's flags and taint, the
  allowlist keyed by site in both directions (a swapped violation at the same
  count fails twice, a site keeps its fingerprint when lines above it move,
  identical sites within a scope and across scopes are told apart), each
  allowlisted site listed once, and the strict set lint-clean and naming only
  existing modules. `tests/test_write_sandbox_guard.py`, itself strict, covers
  the guard's placement and enforcement and the census record the session hook
  writes: its version, roots and writes, an empty record when nothing was
  recorded, and none from an xdist worker.
- `tests/test_check_write_sandbox_census.py` covers write classes (the deepest
  root, `other:` paths, Windows paths), the harness's writes told from a test's,
  the gate holding, failing on a new class with its write and test named and on
  a listed class no shard recorded, a missing or malformed shard record, a class
  without its reason, the job-summary table, and the workflow giving every
  coverage shard a census record that the gate reads.
- `tests/test_write_sandbox_promotions.py` covers the survey's verdict: a module
  joins only when a test of it ran and every test passed, strict modules are not
  named again, allowlisted ones are held back, the lane is red while a module can
  join and green otherwise, an unusable report fails, and the survey workflow
  hands its strict run's report to the verdict.
- `mutation/` is tested as configuration/orchestration through its active
  script/tests and CI workflow. `specs/roadmap/`, `scripts/benchmarks/`, and generated
  artifacts are intentionally not claimed as a current test suite.

## Test write sandbox

Test writes are confined to a per-test scratch directory by three independent
layers. This section constrains what the *test suite* may write. It is distinct
from the product runtime write policy — user-code execution, pickle and joblib
artifact loading, project-root containment — owned by
[sandbox-security](../sandbox-security/high-level.md).

**Layer 1 — convention.** A `haute_scratch` fixture yields a per-test scratch
directory backed by pytest's temporary-path machinery: unique per test,
platform-appropriate, auto-pruned by pytest's retention policy. It is the named
convention every new or converted test write derives from. The raw `tmp_path`
fixture remains legal — it is the same substrate — but `haute_scratch` adds the
stable name, an export of the root as `HAUTE_TEST_SANDBOX_ROOT`, and a seam
where the root could later move without touching call sites.

**Layer 2 — lint.** A source-scan test walks the test tree's AST and flags
writes whose destination cannot be shown to derive from the scratch fixtures,
using conservative intra-function taint tracking:

- Leak-prone temporary-directory and temporary-file creation, unless its
  directory argument is fixture-derived. Self-cleaning temporary-file context
  managers stay legal — layer 3 redirects them into the sandbox at runtime.
- Write-mode `open`, text and byte writes, directory creation, and
  destination-writing copy/move/delete/archive helpers whose target is a string
  literal or a name not reachable from the fixtures.
- Absolute path literals and literals containing parent-directory traversal
  **in filesystem contexts** — arguments to path construction, `open`, and the
  write APIs above. Not bare string literals generally: route literals are
  ubiquitous and are not paths.

It ships with an explicit allowlist for not-yet-converted files, under
`[lint]` in `tests/write_sandbox.toml`, keyed by site: each flagged write's
fingerprint hashes its file, enclosing scope, kind and whitespace-normalised
source, and how many identical sites precede it in that scope, as the debt
ratchet keys its sites. The line is left out, so an edit above a site keeps
its fingerprint. The allowlist is asserted **in both directions**: a site it
does not hold fails, *and* a held site the scan no longer finds fails, so a file
that swaps one violation for another at the same count fails twice. Targets
the scan cannot resolve — helper parameters, call results — deliberately pass
lint; layer 3 owns those.

**Layer 3 — runtime guard.** An autouse fixture, mode-dependent per test:

- **strict** — the working directory moves into the sandbox; temporary-directory
  and home-directory environment variables are pointed inside it; `open` and the
  low-level open call are intercepted, and any write-intent open whose resolved
  path falls outside the sandbox root raises immediately, naming the offending
  path and the test. This catches the computed paths layer 2 cannot see. The
  stores the harness keeps for the test outside its `tmp_path`, which a test
  may list, are inside the sandbox too: its source-proof records, the session's
  source cache and local MLflow store (both moved off the repository root), and
  the widened-root fixture's cache.
- **observe** — the same interception with no working-directory or environment
  changes; out-of-sandbox writes are *recorded*, not failed, and a terminal
  summary prints the census by path. With
  `HAUTE_TEST_WRITE_SANDBOX_CENSUS_OUT` set, the controller also writes the
  run's merged census there as JSON (a version, the repository, home and
  runner-temp roots, and each write's test, call, path and mode), even when it
  recorded nothing. Observe mode allows the run's pytest base directory, the
  system temporary directory and the Hypothesis example database.
- **off** — an escape hatch for debugging.

The mode can be forced for a whole run with
`HAUTE_TEST_WRITE_SANDBOX=strict|observe|off`; without it, modules in the
strict set run strict and everything else runs observe.

Strict membership is the module set `strict` in `tests/write_sandbox.toml`
plus the opt-in `sandbox_strict` marker. Every listed module must exist.
Tests carrying the `perf` marker are exempt from interception so wall-clock
budgets see no overhead. Every test outside `off` mode gets the sandbox root
exported as `HAUTE_TEST_SANDBOX_ROOT`, so an external permission gate can
scope "run the tests" to writes under that root without parsing test internals.

### Known gaps

Stated, not silently omitted:

- **Native-code writes** bypass Python's `open` entirely. In strict mode they
  are still *contained* by the working-directory and temporary-directory
  redirection — relative and temporary paths resolve inside the sandbox — but
  they are not *intercepted*.
- **Rename, directory creation, and file-descriptor-level APIs** are not
  intercepted.
- **Session-scoped fixtures** that run before the per-test guard are outside its
  window.
- **The system temporary directory** is allowed in observe mode, so an observe
  test writing under `tempfile.gettempdir()` is never recorded. Strict mode
  points the temporary directory into the sandbox, which contains those writes.
- **pytest's assertion-rewrite bytecode** (`__pycache__/*.pyc.<pid>`) is
  recorded but never gated: which test triggers it depends on import order and
  worker scheduling, not on what any test does. Multiprocessing's
  shared-memory arenas (`/dev/shm/pym-*`, created and unlinked at once) are let
  through in both modes, as `os.devnull` is: they are the interpreter's own.

These are census-informed ratchet candidates.

### The ratchet

`tests/write_sandbox.toml` holds the ratchet's state, and each of its lists
moves one way:

1. The lint allowlist shrinks monotonically; conversions delete fingerprints,
   and the both-direction assertion enforces it.
2. New test files are born clean — absent from the allowlist, so lint-gated from
   day one.
3. The census may not grow. Every coverage shard records its census and
   uploads it, and the coverage gate's census check
   (`scripts/check_write_sandbox_census.py`) puts each write in a class: a root
   the shard recorded and the first path component below it (`repo:.haute_cache`,
   `home:.cache`, `runner-temp:_runner_file_commands`), or `other:` and the
   path's first two components. A class not listed under `[census]`, with its
   reason, fails the gate, and so does a listed class no shard recorded. A
   missing or malformed shard record fails it too.
4. The strict set grows at each scheduled pass. The monthly write-sandbox
   survey runs the suite with the sandbox forced strict, and
   `scripts/write_sandbox_promotions.py` names each module whose every test
   that ran passed, at least one having run, that is neither strict already
   nor allowlisted by the lint. The lane stays red, with a `sandbox-watch`
   issue, until a pass moves them into `strict`. A module that passes strict
   but still has allowlisted sites is reported: its flagged writes convert
   first.
