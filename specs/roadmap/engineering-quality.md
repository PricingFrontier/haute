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
PR #280.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ENGQ-CI01 | Planned | P1 | The locked Python and npm dependencies carry no advisory that an upgrade within their caps fixes. |
| ENGQ-CI02 | Decision | P1 | Every accepted advisory has a current, owner-approved review date, and the locked advisory audit passes on `main`. |
| ENGQ-CI03 | Planned | P2 | The model-training tests take at most half their present CI time, with every assertion unchanged. |
| ENGQ-CI04 | Planned | P2 | Four tests that failed only under another scheduler, a higher worker count or Python 3.13 pass regardless of their neighbours and runner load. |
| ENGQ-CI05 | Planned | P3 | The compatibility shards are balanced by durations measured without coverage. |
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |

## Planned improvements

`ENGQ-CI01` and `ENGQ-CI02` come first: the locked advisory audit has been red
on `main` since 30 September 2026, and `ENGQ-CI02` needs the owner's decision
on each accepted risk. `ENGQ-CI03` is the largest further cut in CI time and
`ENGQ-CI04` removes known causes of flaky runs; both are independent of the
other packages. `ENGQ-CI05` is balance tuning for the compatibility shards.
`ENGQ-R01` is an independent clean-up, cheapest after the larger refactors
have deleted what they replace. `ENGQ-R05` reorganises the suite under the
coverage rule the [engineering-quality specification](../engineering-quality/high-level.md)
states.

### ENGQ-CI01 — Upgrade the locked dependencies past their fixed advisories
**Why:** The locked advisory audit (the `locked-advisory-audit` job in
`.github/workflows/dependencies.yml`) has been red on `main` since its
scheduled run on 30 September 2026. Besides the expired acceptance that
`ENGQ-CI02` covers, its reports list blocking advisories that have fixed
releases:
- `pyjwt` 2.13.0 has twelve advisories, fixed in 2.14.0 and 2.15.0.
- `urllib3` 2.7.0 has three, fixed in 2.8.0.
- In the frontend lockfile, `brace-expansion` and `undici` have high-severity
  advisories with fixes available.

The audit blocks on every Python advisory and on every high or critical npm
advisory. The frontend also has moderate advisories, which do not block, in
`vitest` and `@vitest/coverage-v8` below 4.1.11.

**Plan:** Upgrade `pyjwt` and `urllib3` in `uv.lock` with
`uv lock --upgrade-package pyjwt --upgrade-package urllib3`. Within the
current caps this resolves `pyjwt` 2.15.1 and `urllib3` 2.8.0, since
`databricks-sql-connector` 4.5.0 accepts `pyjwt>=2.0.0,<3.0.0`. In `frontend/`, run `npm audit fix`
without `--force`, and take `vitest` and `@vitest/coverage-v8` to 4.1.11. The
PR's CI runs the full suites, the browser lanes and the audit on the upgrade.

**Acceptance:** The audit's `pip-audit` and `npm audit` reports contain no
finding for `pyjwt`, `urllib3`, `brace-expansion` or `undici`. `uv.lock` and
`frontend/package-lock.json` change only in those packages, the two Vitest
packages and their dependency closure. CI is green apart from the
acceptances that `ENGQ-CI02` owns.

**Dependencies:** None.

**Evidence:** `.github/workflows/dependencies.yml`;
`scripts/check_dependency_audit.py`; `uv.lock`; `frontend/package-lock.json`.

### ENGQ-CI02 — Renew or retire the accepted dependency advisories
**Why:** `security/accepted-risks.toml` accepts two advisories until a review
date:
- **MLflow PYSEC-2026-3865:** an SSRF in MLflow's AI Gateway proxy, which
  haute never starts or mounts. Its acceptance passed its `review_by` of
  2026-09-29, so since 30 September the audit stops with a policy error
  (exit 2) before it evaluates any finding. MLflow 3.15.1 still has no fixed
  release.
- **cryptography PYSEC-2026-3552:** reaches its `review_by` on 2026-10-02.

A third advisory needs a decision too. `oauthlib` 3.3.1 carries
CVE-2026-49265, which is fixed only in 4.0.0. `databricks-sql-connector`
4.5.0 requires `oauthlib>=3.1.0,<4.0.0`, so no upgrade within the
`databricks` extra's caps fixes it.

**Plan:** For each of these three advisories, first check whether a fixed
release now resolves within the caps, and upgrade if it does. Otherwise,
record an acceptance with the owner's approval, stating the exposure, a
compensating control and a new review date. The owner chooses:
- the review dates;
- whether the `databricks` extra may carry `oauthlib` 3.x until the connector
  admits 4.x.

**Acceptance:** `scripts/check_dependency_audit.py` passes on `main`. Every
entry in `security/accepted-risks.toml` names an owner, an approval date and
a review date that has not passed. The daily scheduled audit is green.

**Dependencies:** `ENGQ-CI01` for the upgradable advisories, and the owner's
decision on each acceptance.

**Evidence:** `security/accepted-risks.toml`;
`scripts/check_dependency_audit.py`; `.github/workflows/dependencies.yml`.

### ENGQ-CI03 — Make the model-training tests cheaper
**Why:** The CI study's per-test timings put most of the backend suite's
cost in a few tests. The slowest 1% (221 tests) take 52% of test time, and
the tests that take a second or more (772 of 22,196) take 79%. About half the
time is in roughly twenty model-training, export and scoring modules:
- `tests/test_model_family_acceptance.py` alone took 377 s of the
  compatibility lane's 3,728 s.
- Its CatBoost lifecycle test took 59 s. A profile of that test shows it
  repeats real native fits, with 36% of its samples in CatBoost's training,
  plus evaluation metrics and SHAP.

The backend lanes' pytest time tripled between August and September while
the test count grew by 49%. Every lane pays for it: the four coverage shards,
the six compatibility shards and the mutation witnesses.

**Plan:** Start from the most expensive modules in
`scripts/test_file_durations.json`, and measure each of their tests from the
coverage shards' JUnit reports. Then:
- Where a test needs a trained model rather than exercising training, share
  one fitted model through a module- or session-scoped fixture.
- Where the asserted property does not depend on data size or fit count,
  shrink the frame and the number of fits.
- Keep one end-to-end training path per model family.

Never weaken an assertion, and keep the mutation targets that cover training
at their survival rates. Refresh the durations file in the same change.

**Acceptance:** The recorded coverage-lane time of the model-training, export
and scoring modules falls by at least half. Every assertion is unchanged, the
mutation targets keep their survival rates, and the durations file is
refreshed.

**Dependencies:** None. `ENGQ-R05` moves some of the same modules
(`tests/test_algorithms_coverage.py`); whichever lands second rebases onto the
other.

**Evidence:**
`tests/test_model_family_acceptance.py::test_native_training_lifecycle_keeps_the_last_good_model`;
`tests/test_modelling.py`; `tests/test_ebm_family.py`;
`tests/test_algorithms_coverage.py`; `tests/test_xgboost_family.py`;
`tests/test_lightgbm_family.py`; `tests/test_training_evaluation.py`;
`scripts/test_file_durations.json`.

### ENGQ-CI04 — Remove order and load dependence from four tests
**Why:** The CI study ran the backend suite under other xdist schedulers and
worker counts, and CI history adds one more case. Four tests failed for
reasons outside the code they test:
- `test_openapi_and_installed_package_versions_match_pyproject` read version
  `0.0.0-dev` instead of the `pyproject.toml` version when `--dist loadfile`
  changed which tests shared its worker. Another test leaks package-version
  state.
- `test_concurrent_training_workers_publish_each_capture_once` was refused by
  memory admission (`ExecutionAdmissionError` for `training_prep`) at six and
  eight workers.
- `test_serve_fails_loudly_when_port_is_bound` failed at eight workers.
- `test_concurrent_broadcast_while_client_disconnects` failed in three CI
  attempts on Python 3.13 on 23 and 24 September, with "live client was
  accidentally removed".

Each is a hidden dependency between tests, or a race that a busier runner, a
different Python version or a different shard layout can expose.

**Plan:** Reproduce each test under the condition that exposed it:
`--dist loadfile`, `-n 8`, or repeated 3.13 runs. Find the leaked state or
the race, and fix it with a regression test that fails first. Fix the
product code when the race is real.

**Acceptance:** In a diagnostic CI run of the full suite under
`--dist loadfile` and at `-n 8`, all four tests pass. The websocket broadcast
test passes 50 repeated runs on 3.13, and any product race found has its own
regression test.

**Dependencies:** None.

**Evidence:**
`tests/test_infrastructure_contracts.py::TestApiRouteContracts::test_openapi_and_installed_package_versions_match_pyproject`;
`tests/test_training_seeding.py::test_concurrent_training_workers_publish_each_capture_once`;
`tests/test_cli_fail_loudly.py::TestServePortConflictDetection::test_serve_fails_loudly_when_port_is_bound`;
`tests/test_server_concurrency.py::TestWsClientsConcurrentMutation::test_concurrent_broadcast_while_client_disconnects`.

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
production caller left.

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
`frontend/src/api/client.ts::checkHauteSession`.

### ENGQ-R05 — Organise tests by behaviour
**Why:** Backend tests total 421,000 lines, 2.3 times the source they test;
`test_optimiser_routes.py` alone is 16,581 lines. About 20,000 lines sit in
files named after coverage campaigns or fix waves rather than behaviour:
`test_expression_parser_coverage.py`, `test_train_service_coverage.py`,
`test_algorithms_coverage.py`, `test_json_cache_coverage_uplift.py`,
`test_coverage_gaps.py`, `test_dry_fixes.py`,
`test_expression_parser_w3_fixes.py`, `test_trace_w4_fixes.py` and eight
more `*_coverage` modules. A developer cannot find the tests for a behaviour
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
