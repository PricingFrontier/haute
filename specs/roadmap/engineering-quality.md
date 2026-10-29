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
PR #280. `ENGQ-CI06`, `ENGQ-R09` and `ENGQ-R11` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md), which also added
evidence to `ENGQ-R01` and `ENGQ-R05`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ENGQ-CI03 | Planned | P2 | The 21 model-training, export and scoring test modules take at most half their recorded coverage-lane time, with no assertion weakened and no training code newly uncovered. |
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |
| ENGQ-CI06 | Planned | P3 | Haute's own workflows pin every action by commit. |
| ENGQ-R09 | Planned | P3 | The write-sandbox ratchet moves: the observe census is recorded and may not grow, modules with an empty census become strict, the system temp directory is a stated gap or no longer allowed, and the lint allowlist is keyed by site. |
| ENGQ-R11 | Decision | P2 | Mutation witnesses cover the rating step and deploy scoring, the modules whose silent failure changes a price, or the specification says why the three JSON-boundary targets suffice. |

## Planned improvements

`ENGQ-CI03` is the largest further cut in CI time and is independent of the
other packages.
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
passes. `TrainService._join_preparation` says shutdown uses it, but only tests
call it; its 10 s default exists for them alone, and an autouse fixture
(`tests/conftest.py::_patient_preparation_join`) rewrites that default to
120 s for every test.

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
`src/haute/modelling/_charts.py::render_ave_feature_svg`;
`src/haute/routes/_training_lifecycle.py::TrainService._join_preparation`;
`tests/conftest.py::_patient_preparation_join`.

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
