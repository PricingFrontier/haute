# Engineering quality roadmap

## Scope

Repository hygiene, the test corpus and its CI runtime, documentation checks,
the coverage gates and the dependency advisory gate. Current behaviour is
specified in
[the engineering-quality specification](../engineering-quality/high-level.md).
`ENGQ-R01` and `ENGQ-R05` come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).
`ENGQ-R11` comes from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md), which also added
evidence to `ENGQ-R01` and `ENGQ-R05`.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |
| ENGQ-R11 | Decision | P2 | Mutation witnesses cover the rating step and deploy scoring, the modules whose silent failure changes a price, or the specification says why the three JSON-boundary targets suffice. |

## Planned improvements

`ENGQ-R01` is an independent clean-up, cheapest after the larger refactors
have deleted what they replace. `ENGQ-R05` reorganises the suite under the
coverage rule the [engineering-quality specification](../engineering-quality/high-level.md)
states.

### ENGQ-R01 — Remove unreferenced and test-only production code
**Why:** Several production functions have no caller at all:
`ensure_execution_context`, `_rating_table_materialises` and `_callable_owner`. Others are called only by tests: `_compute_schema_hash`, `validate_submodel_instances`,
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
production caller left. The 25 October 2026 review added a parameter that is
accepted and never read, `render_ave_feature_svg(is_categorical=...)`, which every
caller computes and passes. `TrainService._join_preparation` says shutdown uses it, but only tests
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
