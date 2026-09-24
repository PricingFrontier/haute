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
| ENGQ-R06 | Planned | P3 | Tests leave the source tree alone: no test writes into the package, and source walks cannot race another worker's bytecode cache. |
| ENGQ-R04 | Planned | P3 | Coverage and documentation gates are pointed at risk and at user-facing documents. |
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |

## Planned improvements

`ENGQ-R06` and `ENGQ-R01` are independent clean-ups; `ENGQ-R01` is cheapest
after the larger refactors have deleted what they replace. `ENGQ-R05` comes
after `ENGQ-R04` has set the coverage rule the reorganised suite must meet.

### ENGQ-R06 — Tests leave the source tree alone
**Why:** Two test-hygiene defects surfaced on 24 September 2026. An ignored
MLflow store (`mlruns/`) appeared inside
`src/haute/assistant/assets/examples/model_lifecycle/` after a local run, so
some test opens an MLflow store rooted in the package tree; the write-sandbox
lint did not catch it because MLflow's default `./mlruns` is relative to the
working directory, not a spelled path. And a CI run on `main` failed
`test_no_forbidden_tokens_in_src` with `FileNotFoundError` on
`src/haute/__pycache__`: the test walks `src/haute` with `rglob("*.py")` while
another xdist worker removes a bytecode cache directory. About fifteen test
modules walk `src/` or `tests/` the same way.

**Plan:** Find the test that points MLflow at the package tree (search for
MLflow use after a `chdir` into packaged example assets, or run the assistant
example tests with a sentinel that fails on a new `mlruns/` under `src/`) and
give it a `tmp_path` tracking URI; extend the MLflow isolation fixture in
`tests/conftest.py` so an unset tracking URI can never default into the
working directory. Give the source-walking tests one helper that lists the
tracked Python files (`git ls-files`, or a walk that prunes `__pycache__`), and
find what deletes `src/haute/__pycache__` during a run.

**Acceptance:** A full CI run leaves no untracked file under `src/`, checked by
the repository-hygiene test; every test that walks source files uses the one
helper; no test deletes a bytecode cache under `src/`.

**Dependencies:** None.

**Evidence:** `tests/conftest.py`; `tests/test_submodel_port_names.py::test_no_forbidden_tokens_in_src`;
`tests/test_repository_hygiene.py`; `tests/test_write_sandbox_lint.py`;
`src/haute/assistant/assets/examples/model_lifecycle`.

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

**Dependencies:** None. The chunked runner stays live (`OPT-P15` kept its
consumer), so the reviewed allowlist covers it.

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

### ENGQ-R04 — Point the gates at risk and at users
**Why:** About 5,000 lines of tests, plus a 1,360-line coverage ledger, keep
the internal specification corpus consistent (`test_docs_accuracy.py`,
`test_workflow_coverage.py`, `test_test_debt.py`, the corpus inventory),
while only one check (`tests/test_node_reference_docs.py`, from `BUILD-R01`)
covers a user-facing document. CI requires 100% statement and branch coverage of changed
code in the execution-critical surface, which rewards line-shaped tests (see
`ENGQ-R05`).

**Decided (24 September 2026):** the 23 September 2026 codebase review's
recommendation. The changed-code gate (100% statement and branch coverage of
changed lines), the per-file critical floors and the mutation targets apply
only to the safety-critical code, whose silent failure changes a computed
price or corrupts persisted work: rating, deploy scoring, feature contracts,
cache identity and snapshot publication. Everywhere else the changed-code
report is shown in the job summary but does not fail the build, and coverage
is judged in review against risk. The global coverage floor stays. The
generated node-reference tables and `mkdocs build --strict` are the
user-facing documentation checks. An internal governance check stays only
if it guards something a user or a later change relies on.

**Plan:** State the rule and the safety-critical module list in the
engineering-quality specification. Rewrite
`[tool.haute.changed_coverage].paths` and the critical-coverage file list to
that set (the changed-code list still names the deleted
`_dataframe_execution_cache.py`), narrow the mutation target plan to it, and
make `check_changed_coverage.py` report without failing for other paths.
Review `test_docs_accuracy.py`, `test_workflow_coverage.py` with its ledger,
`test_test_debt.py` and the corpus inventory against the retention rule, and
record in the specification which checks stay and why; delete the rest.

**Acceptance:** The engineering-quality specification states the coverage
rule, the safety-critical list and which documentation checks cover
user-facing documents; CI enforces exactly that; each internal governance
check still in the suite has a recorded reason.

**Dependencies:** None.

**Evidence:** `tests/test_docs_accuracy.py`; `tests/test_workflow_coverage.py`;
`tests/workflow_coverage.toml`; `tests/test_test_debt.py`;
`scripts/check_changed_coverage.py`; `scripts/check_critical_coverage.py`;
`scripts/run_mutation_suite.py`; `scripts/spec_corpus_inventory.py`;
`pyproject.toml` (`[tool.haute.changed_coverage]`,
`[tool.haute.critical_coverage]`); `.github/workflows/ci.yml`.

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
