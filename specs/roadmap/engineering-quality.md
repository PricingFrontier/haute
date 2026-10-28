# Engineering quality roadmap

## Scope

Repository hygiene, the test corpus and its CI runtime, documentation checks,
the coverage gates and the dependency advisory gate. Current behaviour is
specified in
[the engineering-quality specification](../engineering-quality/high-level.md).
`ENGQ-R01` and `ENGQ-R05` come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md). The
`ENGQ-CI` packages come from the 30 September 2026 CI duration study: per-test
timings from diagnostic PR #279 (run 36769548458) and the sharded lanes of
PR #280. `ENGQ-CI06`, `ENGQ-R06` and `ENGQ-R07` to `ENGQ-R11` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md), which also added
evidence to `ENGQ-R01` and `ENGQ-R05`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ENGQ-CI03 | Planned | P2 | The 21 model-training, export and scoring test modules take at most half their recorded coverage-lane time, with no assertion weakened and no training code newly uncovered. |
| ENGQ-CI04 | Planned | P2 | Three tests that failed only under another xdist scheduler or a higher worker count pass regardless of their neighbours and runner load. |
| ENGQ-CI05 | Planned | P3 | The compatibility shards are balanced by durations measured without coverage. |
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |
| ENGQ-CI06 | Planned | P3 | Haute's own workflows pin every action by commit. |
| ENGQ-R06 | Planned | P3 | The build hook fails with the install hint when Node is missing instead of guessing a machine path. |
| ENGQ-R07 | Planned | P2 | Every gate runs on every run: a red coverage shard fails the coverage gate instead of skipping it, the documentation site builds strictly on every pull request, the warnings filter holds in the exploration lane and in ledger evidence, and the two safety-critical coverage lists agree with each other and the specification. Core-only dependency smoke stays free of optional extras and cannot pass with every test skipped. |
| ENGQ-R08 | Planned | P2 | The suite exercises what production runs: a representative route slice under the process execution mode, the production preparation-join bound, a flaky browser test made visible, and fixture git commands that fail loudly. |
| ENGQ-R09 | Planned | P3 | The write-sandbox ratchet moves: the observe census is recorded and may not grow, modules with an empty census become strict, the system temp directory is a stated gap or no longer allowed, and the lint allowlist is keyed by site. |
| ENGQ-R10 | Planned | P3 | Repository-health hygiene: file-reading tests carry the `meta` marker, the specification's bundle and lint numbers are read from their files, `mypy` covers `scripts/`, the package smoke derives its version prefixes, the Windows preflight is held in step, the dependency alarm fires on schedule only, and the e2e server stops its Node child on Windows. |
| ENGQ-R11 | Decision | P2 | Mutation witnesses cover the rating step and deploy scoring, the modules whose silent failure changes a price, or the specification says why the three JSON-boundary targets suffice. |
| ENGQ-R12 | Planned | P2 | The UC create-only release gate requires competing creates and distinguishes conflict refusals from transport/auth failures. |

## Planned improvements

`ENGQ-CI03` is the largest further cut in CI time and
`ENGQ-CI04` removes known causes of flaky runs; both are independent of the
other packages. `ENGQ-CI05` is balance tuning for the compatibility shards.
`ENGQ-R01` is an independent clean-up, cheapest after the larger refactors
have deleted what they replace. `ENGQ-R05` reorganises the suite under the
coverage rule the [engineering-quality specification](../engineering-quality/high-level.md)
states.

### ENGQ-CI03 — Make the model-training tests cheaper
**Why:** The CI study's per-test timings put most of the backend suite's
cost in a few tests. The slowest 1% (221 tests) take 52% of test time, and
the tests that take a second or more (772 of 22,196) take 79%. The largest
group is model training, export and scoring. The 21 modules listed under
Evidence (the cohort) took:
- 1,878 s of the study's 3,728 s compatibility lane (50%);
- 1,128.1 s of 3,113.5 s (36%) in the coverage lane, as recorded in
  `scripts/test_file_durations.json` at `02f2d0ed2`, from PR #280's run
  36778804735.

`tests/test_model_family_acceptance.py` is the largest of them. Its CatBoost
lifecycle test took 59 s, and a profile of it shows repeated real native
fits, with 36% of its samples in CatBoost's training, plus evaluation
metrics and SHAP. The backend lanes' pytest time tripled between August and
September while the test count grew by 49%. Every lane pays for it: the four
coverage shards and the six compatibility shards.

**Plan:** Measure each cohort test from the coverage shards' JUnit reports,
starting with the most expensive module. Then:
- Where a test needs a trained model rather than exercising training, share
  one fitted model through a module- or session-scoped fixture.
- Where the asserted property does not depend on data size or fit count,
  shrink the frame and the number of fits.
- Keep one end-to-end training path per model family.

No assertion is removed or weakened. The mutation targets in
`mutation/targets.json` do not cover training, so coverage and seeded faults
stand in for them, as the acceptance below states. Refresh the durations file
in the same change.

**Acceptance:** The measure is the cohort's summed coverage-lane time in a
durations file refreshed from one green coverage-lane run. It is at most
half the 1,128.1 s baseline. In addition:
- The coverage gate's `--show-missing` report shows no statement or branch
  newly missed, compared with the baseline run. This covers
  `src/haute/modelling/` and the training routes: `src/haute/routes/_train_service.py`,
  `src/haute/routes/_training_artifacts.py`,
  `src/haute/routes/_training_evaluation.py`,
  `src/haute/routes/_training_lifecycle.py`,
  `src/haute/routes/_training_preparation.py` and
  `src/haute/routes/_training_worker.py`.
- For each module converted to a shared fitted model, one deliberately
  broken training step that its tests caught before is still caught.

**Dependencies:** None. `ENGQ-R05` moves some of the same modules
(`tests/test_algorithms_coverage.py`); whichever lands second rebases onto the
other and keeps the cohort's measure comparable.

**Evidence:**
`tests/test_model_family_acceptance.py::test_native_training_lifecycle_keeps_the_last_good_model`;
`tests/test_modelling.py`; `tests/test_ebm_family.py`;
`tests/test_algorithms_coverage.py`; `tests/test_xgboost_family.py`;
`tests/test_lightgbm_family.py`; `tests/test_modelling_train_score_contract.py`;
`tests/test_training_evaluation.py`; `tests/test_training_seeding.py`;
`tests/test_modelling_export.py`; `tests/test_glm_integration.py`;
`tests/test_modelling_routes.py`; `tests/test_model_family_review_fixes.py`;
`tests/test_offset_scoring.py`; `tests/test_training_temp_cleanup.py`;
`tests/test_train_param_routing.py`;
`tests/test_binary_classification_contract.py`;
`tests/test_rustystats_algorithm.py`; `tests/test_target_task_gate.py`;
`tests/test_mlflow_log_button_roundtrip.py`;
`tests/test_training_contract_per_model.py`;
`scripts/test_file_durations.json`; `mutation/targets.json`.

### ENGQ-CI04 — Remove order and load dependence from three tests
**Why:** The CI study ran the backend suite under other xdist schedulers and
worker counts. Three tests failed for reasons outside the code they test:
- `test_openapi_and_installed_package_versions_match_pyproject` read version
  `0.0.0-dev` instead of the `pyproject.toml` version when `--dist loadfile`
  changed which tests shared its worker. Another test leaks package-version
  state.
- `test_concurrent_training_workers_publish_each_capture_once` was refused by
  memory admission (`ExecutionAdmissionError` for `training_prep`) at six and
  eight workers.
- `test_serve_fails_loudly_when_port_is_bound` failed at eight workers.

Each is a hidden dependency between tests, or a race that a busier runner or
a different shard layout can expose.

**Plan:** Reproduce each test under the condition that exposed it:
`--dist loadfile` for the first, `-n 8` for the other two. Find the leaked
state or the race, and fix it with a regression test that fails first. Fix
the product code when the race is real.

**Acceptance:** In a diagnostic CI run of the full suite under
`--dist loadfile` and at `-n 8`, all three tests pass. Any product race found
has its own regression test.

**Dependencies:** None.

**Evidence:**
`tests/test_infrastructure_contracts.py::TestApiRouteContracts::test_openapi_and_installed_package_versions_match_pyproject`;
`tests/test_training_seeding.py::test_concurrent_training_workers_publish_each_capture_once`;
`tests/test_cli_fail_loudly.py::TestServePortConflictDetection::test_serve_fails_loudly_when_port_is_bound`.

### ENGQ-CI05 — Balance the compatibility shards on their own durations
**Why:** One durations table, measured on the coverage lane, balances every
sharded lane (`tests/_ci_shards.py`). Coverage tracing slows some modules far
more than others. In the CI study, these modules ran longer under coverage:

| Module | Slowdown under coverage |
|---|---:|
| `tests/test_chunk_whitelist_proofs.py` | 1.65× |
| `tests/test_test_debt.py` | 2.2× |
| `tests/test_repository_hygiene.py` | 2.6× |

The compatibility shards run without coverage, so a shard holding those
modules finishes early. On PR #280's second run, the 3.13 shards took 4.0, 6.1
and 6.4 minutes, and the 3.11 shards 4.0, 6.0 and 5.7.

**Plan:** Have the compatibility shards upload JUnit reports as the coverage
shards do. Extend `scripts/test_file_durations.json` to one table per lane,
let the shard option name the table, and refresh both tables with
`scripts/refresh_test_durations.py`. The partition rule and its tests are
unchanged.

**Acceptance:** On two consecutive CI runs, each compatibility interpreter's
slowest shard is within 15% of its fastest. `tests/test_ci_shards.py` covers
table selection and a missing table.

**Dependencies:** None.

**Evidence:** `tests/_ci_shards.py`; `scripts/test_file_durations.json`;
`scripts/refresh_test_durations.py`; `.github/workflows/ci.yml`.

### ENGQ-R01 — Remove unreferenced and test-only production code
**Why:** Several production functions have no caller at all:
`ensure_execution_context`, `_rating_table_materialises`, `_callable_owner`,
and the git guard `_assert_not_protected` (the guard is enforced through
`_is_protected` instead). Others are called only by tests: `_compute_schema_hash`, `validate_submodel_instances`,
`remove_config_file`, `find_config_by_func_name`, the `_ram_estimate` helpers
`_parquet_metadata`, `_resolve_edge_join_column_names` and
`_resolve_target_column_names`, `wrap_path_case_audit`, and the registry's
`get_exec` and `get_codegen`. Twenty-five `*_for_tests`, `_reset_*` and `_clear_*` hooks live
in production modules, and `routes/_train_service.py` is a compatibility
facade that re-exports private names mainly for tests. In the frontend, knip
reports 8 unused files (including the banding and rating editor barrels), the
whole `panels/editors/index.ts` barrel of 22 editors (consumers use the lazy
editors), 33 unused exports, 91 unused exported types and an unlisted
`@lezer/highlight` dependency. The API client's `checkHauteSession` has no
production caller left. The 25 October 2026 review added two parameters that are
accepted and never read, `_resolve_node_config(n_params=...)` and
`render_ave_feature_svg(is_categorical=...)`, which every caller computes and
passes.

**Plan:** Delete the unreferenced code. For test-only functions, either move
the tests to the production entry point the function was meant to serve or
delete both. Replace the test hooks with fixtures that reset state from
outside, and remove the training facade by moving its importers to the
owning modules. Add knip, or an equivalent, to the frontend lint so dead
exports stay visible.

**Acceptance:** vulture at the agreed confidence and knip report no
unreferenced production code beyond a reviewed allowlist; the training facade
is gone; tests import the owning modules.

**Dependencies:** None.

**Evidence:** `src/haute/_execution_context.py::ensure_execution_context`;
`src/haute/_rating.py::_rating_table_materialises`;
`src/haute/_polars_io_registry.py::_callable_owner`;
`src/haute/_git_core.py::_assert_not_protected`;
`src/haute/_model_scorer.py::_compute_schema_hash`;
`src/haute/_submodel_instances.py::validate_submodel_instances`;
`src/haute/_config_io.py::remove_config_file`;
`src/haute/_config_io.py::find_config_by_func_name`;
`src/haute/_ram_estimate.py::_parquet_metadata`;
`src/haute/_ram_estimate.py::_resolve_edge_join_column_names`;
`src/haute/_ram_estimate.py::_resolve_target_column_names`;
`src/haute/_path_case_audit.py::wrap_path_case_audit`;
`src/haute/_registry.py::get_exec`; `src/haute/routes/_train_service.py`;
`frontend/src/panels/editors/index.ts`;
`frontend/src/panels/editors/banding/index.ts`;
`frontend/src/panels/editors/rating/index.ts`;
`frontend/src/api/client.ts::checkHauteSession`;
`src/haute/_config_builder.py::_resolve_node_config`;
`src/haute/modelling/_charts.py::render_ave_feature_svg`.

### ENGQ-R05 — Organise tests by behaviour
**Why:** Backend tests total 421,000 lines, 2.3 times the source they test;
`test_optimiser_routes.py` alone is 16,581 lines. About 20,000 lines sit in
files named after coverage campaigns or fix waves rather than behaviour:
`test_expression_parser_coverage.py`, `test_train_service_coverage.py`,
`test_algorithms_coverage.py`, `test_json_cache_coverage_uplift.py`,
`test_coverage_gaps.py`, `test_dry_fixes.py`,
`test_expression_parser_w3_fixes.py`, `test_trace_w4_fixes.py` and eight
more `*_coverage` modules; the 25 October 2026 review's sweep counted 35 modules
named after a campaign or a fix wave. A developer cannot find the tests for a behaviour
by name, and a full serial run takes about forty minutes.

**Plan:** Merge campaign-named modules into the component and behaviour
modules that own what they test, removing duplicates found on the way, and
split the largest modules by behaviour. Record the target structure in the
engineering-quality specification.

**Acceptance:** No test module is named after a coverage campaign or fix
wave; no module exceeds an agreed size; coverage of the safety-critical
modules is unchanged; suite runtime is recorded before and after.

**Dependencies:** None.

**Evidence:** `tests/test_optimiser_routes.py`;
`tests/test_expression_parser_coverage.py`;
`tests/test_train_service_coverage.py`; `tests/test_algorithms_coverage.py`;
`tests/test_json_cache_coverage_uplift.py`; `tests/test_coverage_gaps.py`;
`tests/test_dry_fixes.py`; `tests/test_expression_parser_w3_fixes.py`;
`tests/test_trace_w4_fixes.py`.

### ENGQ-CI06 — Actions pinned by commit
**Why:** Haute's own workflows pin actions by floating major tag
(`actions/checkout@v4`, `astral-sh/setup-uv@v4`, `actions/upload-artifact@v4`; a
hundred such lines across `.github/workflows`), so a compromised tag runs with
the release and container-registry secrets. The templates `haute init` writes
are `CLI-R06`.

**Plan:** Pin each action to a commit SHA with the tag in a comment and let
Dependabot keep them fresh.

**Acceptance:** Every `uses:` under `.github/workflows` names a 40-hex commit; a
lint step fails on a tag pin.

**Dependencies:** None.

**Evidence:** `.github/workflows/ci.yml`; `.github/workflows/release.yml`;
`.github/workflows/container-smoke.yml`.

### ENGQ-R06 — The build hook fails like the CLI
**Why:** When Node is not on `PATH`, the build hook falls back to
`C:\Program Files\nodejs\npm.cmd` and prepends that directory to `PATH`, the
machine-layout fallback the CLI's `_npm` helper deliberately removed because it
hides the real problem.

**Plan:** Fail with the same install hint as the CLI.

**Acceptance:** A build without Node on `PATH` stops with the hint; no path under
`Program Files` appears in the hook.

**Dependencies:** None.

**Evidence:** `hatch_build.py::_npm`; `hatch_build.py::_node_env`;
`src/haute/cli/_helpers.py::_npm`.

### ENGQ-R07 — Every gate runs on every run
**Why:** The `backend-coverage-gate` job has no `if:` and the shard's coverage artifact is
uploaded without `if: always()`, so one red coverage shard skips the 90 % floor, the
per-file critical floors and the changed-code gate entirely and withholds the coverage
data; a pull request red for an unrelated flaky test learns about an uncovered
safety-critical line only on the next cycle. The mutation gate already protects itself
against this (`!cancelled()` plus explicit failure steps) and the specification promises
it for mutation alone. `mkdocs build --strict` runs only after a merge to `main` and only
when `docs/` or `mkdocs.yml` changed, so a broken nav entry or dead link merges green and
is found by a red Pages deploy. The weekly exploration lane and all 31 ledger evidence
commands pass `-p no:warnings`, which unloads the warnings plugin, so the `RuntimeWarning`
error filter the pull-request lane applies does not hold there. The changed-code gate's
35 paths and the 34 critical floors differ by `_workbench_visibility.py`, which the
specification's "exactly this set" does not name, and no test keeps the two lists equal
(both checker tests build synthetic `pyproject.toml` files). From the [25 October 2026
codebase review](codebase-review-2026-10-25.md) (ENG-1, ENG-2, ENG-4, ENG-8).

The [28 October review](codebase-review-2026-10-28.md) found another vacuous gate:
`optional-deps-smoke` syncs without `dev`, then invokes bare `uv run pytest`,
which syncs the default dev group containing `haute[databricks]` back into the
environment. The test module skips all three tests when the SQL connector is
present, including its assertion that the connector must be absent. The exact
test invocation in the local dev environment exits zero with three skips. Hosted
[main run 40754355254, optional-deps-smoke](https://github.com/PricingFrontier/haute/actions/runs/40754355254/job/123049170185)
confirms the workflow consequence: its `uv run` step uninstalled 5 packages,
installed 42, then passed with `3 skipped in 0.18s`.

**Plan:** Upload each shard's coverage and JUnit files `if: always()`, run the gate job
`if: !cancelled()` and fail its first step explicitly when any shard did not succeed. Add
a `docs-build` job to `ci.yml` running `mkdocs build --strict`, or run the docs build on
`pull_request` too. Replace `-p no:warnings` with an ignore of the specific Hypothesis
warning, and have the ledger validator reject evidence commands that disable the plugin.
Seed a floor for `_workbench_visibility.py` and name it in the specification, or drop it
from the paths; add a `meta` test asserting the two lists and the specification agree.
Prepare the optional-dependency lane with the core project and the test harness's
explicit dependency closure, including Hypothesis, which `tests/conftest.py`
imports unconditionally. Do not restore the full dev group to obtain those test
dependencies. Then invoke pytest without resyncing project groups (for example
`uv run --no-sync pytest ...`).
Add a lane-level environment assertion that fails when core MLflow is absent or
Databricks SQL is installed, outside the module's development-environment skip.
Keep ordinary full-development test runs able to skip this lane-specific module.

**Acceptance:** A workflow run with one failed shard shows the coverage gate failed, not
skipped, with every shard's artifact present; a pull request with a broken docs nav entry
is red; a `RuntimeWarning` fails the exploration lane; the list-parity test passes and
fails when one list gains a path.
In a fresh core-only environment the shared test setup imports successfully and
all three optional-dependency tests execute and pass; intentionally installing
the Databricks connector makes that lane fail,
not pass with skips. Verify the actual workflow command sequence in an isolated
environment rather than relying on a workflow-text assertion alone.

**Dependencies:** None.

**Evidence:** `.github/workflows/ci.yml`, `.github/workflows/mutation.yml`,
`.github/workflows/docs.yml`, `.github/workflows/property-exploration.yml`,
`tests/workflow_coverage.toml`, `pyproject.toml`, `tests/test_check_changed_coverage.py`,
`tests/test_check_critical_coverage.py`, `tests/test_optional_dependency_matrix.py`,
`tests/conftest.py`, `specs/engineering-quality/high-level.md`.

### ENGQ-R08 — The suite exercises what production runs
**Why:** An autouse fixture runs every preview, trace and estimate in `thread` mode while
production defaults to `process`; only 21 sites in 11 modules opt back in, so the pool's
result serialisation, worker preload and environment propagation are exercised by a few
dozen tests and a route defect that shows only across the process boundary passes the
route suite; the engineering-quality specification does not mention the fixture. A second
autouse fixture rewrites the training service's preparation-join bound from 10 s to 120 s
for every test, so the production bound is never exercised. Playwright retries twice in CI
and nothing reports a test that passed on its third attempt; `failOnFlakyTests` is
available and unset. The test helper `git_run` runs git without `check=True`, so a failed
`init` or `commit` in fixture setup surfaces later as an unrelated assertion. From the
[25 October 2026 codebase review](codebase-review-2026-10-25.md) (ENG-5, ENG-6, ENG-10,
ENG-16).

**Plan:** Document the thread-mode fixture in the specification and run one
representative route slice (preview, trace, node data, optimiser estimate) under `process`
mode in a lane, or make the fixture opt-out per module. Keep the production join bound and
widen it only for the slow training-lifecycle tests, with one test pinning the default. On
push to `main` set `failOnFlakyTests`, and on pull requests write the flaky titles to the
job summary. `git_run` raises with stderr on failure.

**Acceptance:** The route slice passes under `process` mode in CI; a test pinning the 10 s
default exists; a browser test that passes only on retry is red on `main` and named in
the pull-request summary; a failing fixture git command raises at the call.

**Dependencies:** None.

**Evidence:** `tests/conftest.py::_interactive_execution_test_mode`,
`src/haute/_interactive_workers.py::resolve_interactive_execution_mode`,
`tests/conftest.py::_patient_preparation_join`, `frontend/playwright.config.ts`,
`tests/_git_helpers.py::git_run`, `specs/engineering-quality/low-level.md`.

### ENGQ-R09 — The write-sandbox ratchet moves
**Why:** Strict interception covers 4 of 603 test modules: `STRICT_FILES` has had no
addition since 13 July 2026 and lost a module on 23 September. Observe mode allows the
whole system temp directory, so a test writing to `tempfile.gettempdir()` is never
recorded, and the specification's "Known gaps" does not say so. The observe census is
printed to the terminal and never persisted or asserted on CI. The lint allowlist is
count-based, so a file that swaps one violation for another at the same count passes
in both directions. From the [25 October 2026 codebase
review](codebase-review-2026-10-25.md) (ENG-7).

**Plan:** Record the census as a CI artifact and assert it does not grow; move modules
with an empty census into `STRICT_FILES` on a schedule; add the system temp directory to
the Known gaps or stop allowing it in observe mode; key the allowlist by a
`file:line:kind` fingerprint as the debt test does.

**Acceptance:** The census artifact exists on every coverage run and a new observe-mode
write fails the gate; the strict set grows at each scheduled pass; the lint fails on a
swapped violation.

**Dependencies:** None.

**Evidence:** `tests/_write_sandbox.py::STRICT_FILES`, `tests/conftest.py`,
`tests/test_write_sandbox_lint.py::EXPECTED_VIOLATIONS`,
`specs/engineering-quality/low-level.md`.

### ENGQ-R10 — Repository-health hygiene
**Why:** The `meta` marker exists so file-reading repository-health checks do not fail
identically on all six compatibility shards, yet only three modules carry it; the two
heaviest scans (the debt scan and the write-sandbox lint, each with a 180 s timeout) and
four other file readers run on every shard, so one stale summary turns seven jobs red.
The specification states bundle ceilings of 285 and 1,581 KiB and "thirteen" lint
exceptions while the checker's defaults are 298 and 1,703 KiB and the config has twelve;
no test pins either. `mypy` excludes `scripts/` (12,486 annotated lines of CI gates) and
the test helpers. The package smoke hand-copies engine version prefixes the dependency
caps already state. The CI/preflight parity test reads `preflight.sh` only, so
`preflight.ps1` can drift unnoticed. The unlocked-resolve alarm fires on any failure
rather than on schedule or `force_fail` as the other monitoring lanes do. The e2e server
script stops Vite with `terminate()` on the npm launcher, which on Windows leaves the Node
child holding the port. From the [25 October 2026 codebase
review](codebase-review-2026-10-25.md) (ENG-3, ENG-9, ENG-11, ENG-12, ENG-13, ENG-14,
ENG-15).

**Plan:** `pytestmark = pytest.mark.meta` on the pure file readers and per-test marks in
the two mixed modules, with an assertion that every test importing the scanners is
marked. Replace the specification's hard numbers with references to the files, or pin
them with a test. Add `scripts/` to the `mypy` invocation. Derive the smoke's expected
versions from the project's specifiers. Parse `preflight.ps1` in the parity test. Use the
shared alarm condition. Reuse the tree-kill helper on `win32`.

**Acceptance:** The compatibility lanes deselect every file-reading check; the
specification carries no number the files contradict; `mypy scripts/` passes in CI; a
cap bump needs no script edit; a drift between the two preflight scripts fails the parity
test.

**Dependencies:** None.

**Evidence:** `pyproject.toml`, `tests/test_test_debt.py`, `tests/test_write_sandbox_lint.py`,
`tests/test_docs_accuracy.py`, `frontend/scripts/check-bundle-size.mjs`,
`frontend/eslint.config.js`, `specs/engineering-quality/low-level.md`,
`scripts/package_smoke_check.py`, `tests/test_frontend_bundle_budget_ci.py`,
`scripts/preflight.ps1`, `.github/workflows/dependencies.yml`,
`scripts/run_frontend_e2e_server.py::_terminate_process`, `scripts/init_smoke.py`.

### ENGQ-R11 — Mutation witnesses for the price-changing modules
**Why:** The safety-critical list has 35 modules but mutation witnesses cover three
JSON-boundary targets only; the rating step and deploy scoring, the modules whose silent
failure changes a price, have coverage floors but no mutation budget. The specification
states the set deliberately, so this is a scope decision rather than drift. From the
[25 October 2026 codebase review](codebase-review-2026-10-25.md) (ENG-17).

**Plan:** Decide. Either add a `rating` target (the step is pure Polars over small
frames, so a witness command is cheap) and a deploy-scoring target before production
use, or state in the specification why the three targets suffice.

**Acceptance:** One of the two, with the specification's mutation sentence saying which.

**Dependencies:** None.

**Evidence:** `mutation/targets.json`, `pyproject.toml`, `src/haute/_rating.py`,
`src/haute/deploy/_scorer.py`, `specs/engineering-quality/high-level.md`.

### ENGQ-R12 — Storage qualification proves actual contention

**Why:** The qualification script accepts exactly one upload success regardless of how other
writers failed, discards their detailed errors, and reports QUALIFIED. The real threaded script
with an offline transport witness returns success for one stored object plus three
PermissionErrors before upload. Zero rounds also qualifies. This gate therefore cannot
establish the create-only guarantee required by hosted storage.

**Plan:** Require at least two writers and one round; require every losing create to have the
documented already-exists outcome. Treat permission, timeout and other transport failures as
failed/inconclusive qualification with visible writer diagnostics. Keep winner-byte
verification and make cleanup ownership explicit.

**Acceptance:** A transport with one winner and conflict refusals qualifies after all
configured rounds. One winner plus any permission/timeout failure does not qualify and returns
nonzero with the error. Zero rounds, fewer than two writers, multiple winners and mismatched
stored bytes cannot pass. These offline cases test gate logic; actual UC qualification still
requires the separate live run.

**Dependencies:** None.

**Evidence:** `scripts/uc_create_only_qualification.py::run_round.worker`; `scripts/uc_create_only_qualification.py::run_round`; `scripts/uc_create_only_qualification.py::qualify`; `specs/hosted-project-storage/high-level.md` create-only qualification boundary.
