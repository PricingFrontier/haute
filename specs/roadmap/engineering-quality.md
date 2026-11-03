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
evidence to `ENGQ-R01` and `ENGQ-R05`. `ROAD-WORKER-05`, also from the 23 September
review, moved here when the background-jobs roadmap retired (2 November 2026); its
behaviour is specified by the
[background-jobs](../background-jobs/high-level.md) and
[execution-engine](../execution-engine/high-level.md) specifications.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| ENGQ-R01 | Planned | P3 | Production code that nothing calls, or only tests call, is removed. |
| ENGQ-R05 | Planned | P3 | Tests are organised by component and behaviour, not by coverage campaign. |
| ENGQ-R11 | Decision | P2 | Mutation witnesses cover the rating step and deploy scoring, the modules whose silent failure changes a price, or the specification says why the three JSON-boundary targets suffice. |
| ROAD-WORKER-05 | Planned | P3 | One worker primitive and one worker-failure taxonomy serve one-shot, pooled and job workers. |

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
reports unused files (besides the node editors' barrels, since removed), 33
unused exports and 91 unused exported types. The API client's `checkHauteSession` has no
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

### ROAD-WORKER-05 — One worker primitive and one failure taxonomy
**Why:** Three subprocess mechanisms carry parallel error hierarchies. The
one-shot `run_isolated_worker` has ten `IsolatedWorker*Error` classes; the warm
`InteractiveWorkerPool` has ten `InteractiveWorker*Error` classes, six of which mirror
them (start, timeout, stopped, memory limit, crashed, remote), and the dedicated worker
reuses them; and the versioned job transport `run_worker_protocol` adds its own. The async
adapter, the job supervisor and the thread timeout helper sit on top. Each taxonomy is
mapped to HTTP status separately; a remote exception's terminal reason is already one
rule (`remote_failure_terminal_reason`, from the child's own `MemoryError` check). This
package comes from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).

**Plan:** Make the warm pool the single primitive, with one-shot work as a
pool slot that is retired after one use, and the versioned transport as the
message format every worker speaks. Replace the parallel hierarchies with one
worker-failure family mapped once to HTTP and job states.

**Acceptance:** One worker-failure hierarchy exists and one mapping to HTTP
and job terminal states; preview, trace, Explore, JSON-cache, output-write,
training and deploy batch workers use the same primitive; the existing
supervision, timeout, memory-limit and crash-classification tests pass
against it.

**Dependencies:** None. The optimiser's input estimate already runs on the
warm interactive worker pool. `PCFG-R04` waits on it in the
[delivery plan](delivery-plan.md), so the worker request format changes once.

**Evidence:** `src/haute/_worker_isolation.py::run_isolated_worker`;
`src/haute/_interactive_workers.py::InteractiveWorkerPool`;
`src/haute/_worker_protocol.py::run_worker_protocol`;
`src/haute/routes/_background_jobs.py::IsolatedJobSupervisor`;
`src/haute/routes/_isolated_worker_async.py`; `src/haute/routes/_timeouts.py`;
`src/haute/_dedicated_workers.py::DedicatedWorker` (the optimiser's solver session,
which already reuses the warm pool's error family).
