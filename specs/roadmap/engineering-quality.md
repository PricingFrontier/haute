# Engineering quality roadmap

## Scope

Repository hygiene, the test corpus, documentation checks and the coverage
gates. Current behaviour is specified in
[the engineering-quality specification](../engineering-quality/high-level.md).
These packages come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R04 | Decision | P3 | Coverage and documentation gates are pointed at risk and at user-facing documents. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |

## Planned improvements

`ENGQ-R01` is an independent clean-up. `ENGQ-R05` is easier after
`ENGQ-R04` has set the coverage rule the reorganised suite must meet.

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
`@lezer/highlight` dependency.

**Plan:** Delete the unreferenced code. For test-only functions, either move
the tests to the production entry point the function was meant to serve or
delete both. Replace the test hooks with fixtures that reset state from
outside, and remove the training facade by moving its importers to the
owning modules. Add knip, or an equivalent, to the frontend lint so dead
exports stay visible.

**Acceptance:** vulture at the agreed confidence and knip report no
unreferenced production code beyond a reviewed allowlist; the training facade
is gone; tests import the owning modules.

**Dependencies:** None. `EXEC-R03` (execution engine) removes the chunked
runner if `OPT-P15` retires its consumer; this package does not wait for it,
and the reviewed allowlist covers the runner while it remains live.

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
`frontend/src/panels/editors/rating/index.ts`.

### ENGQ-R04 — Point the gates at risk and at users
**Why:** About 5,000 lines of tests, plus a 1,360-line coverage ledger, keep
the internal specification corpus consistent (`test_docs_accuracy.py`,
`test_workflow_coverage.py`, `test_test_debt.py`, the corpus inventory),
while only one check (`tests/test_node_reference_docs.py`, from `BUILD-R01`)
covers a user-facing document. CI requires 100% statement and branch coverage of changed
code in the execution-critical surface, which rewards line-shaped tests (see
`ENGQ-R05`).

**Plan:** Decide the coverage rule: keep mutation and critical-file ratchets
for the safety-critical code (rating, deploy scoring, feature contracts,
cache identity) and use risk-based review elsewhere, or keep the current
gate with a reason. Decide which further documentation checks should cover
`docs/`. Review whether each internal governance check still pays for
its maintenance.

**Acceptance:** The engineering-quality specification states the coverage
rule and which documentation checks cover user-facing documents; CI enforces
it.

**Dependencies:** None.

**Evidence:** `tests/test_docs_accuracy.py`; `tests/test_workflow_coverage.py`;
`tests/workflow_coverage.toml`; `tests/test_test_debt.py`;
`scripts/check_changed_coverage.py`; `scripts/spec_corpus_inventory.py`.

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
wave; no module exceeds an agreed size; coverage of the critical surface is
unchanged; suite runtime is recorded before and after.

**Dependencies:** `ENGQ-R04`.

**Evidence:** `tests/test_optimiser_routes.py`;
`tests/test_expression_parser_coverage.py`;
`tests/test_train_service_coverage.py`; `tests/test_algorithms_coverage.py`;
`tests/test_json_cache_coverage_uplift.py`; `tests/test_coverage_gaps.py`;
`tests/test_dry_fixes.py`; `tests/test_expression_parser_w3_fixes.py`;
`tests/test_trace_w4_fixes.py`.
