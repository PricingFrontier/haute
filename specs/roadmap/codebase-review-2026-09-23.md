# Haute codebase review — 23 September 2026

Reviewed at `main` @ `9319b11d`. The review read the code and changed none of it. This is a dated supporting report: what remains of it are the findings still open, each tracked as a package in its component roadmap and listed in [section 8](#8-where-each-finding-is-tracked); those packages, not this report, carry the plans. Findings whose packages were delivered have been removed, and the remaining sections keep their original numbers.

## How the review was done

- **Specs first.** I read the high-level spec of every component (the execution engine, caching, IO, pipeline-config, codegen, expression-parsing, server-api, tracing, JSON shredding, background jobs, sandbox, modelling, optimiser, deploy and engineering-quality specs in full; the rest via their Purpose and Design-rationale sections), the specs README, the roadmap index and the PR #227 review documents, so that tracked work is not reported as new.
- **Mechanical metrics.** radon cyclomatic complexity, jscpd clone detection (Python and TS, without size limits), vulture (Python dead code), knip (frontend dead exports), 3-month git churn, and reference counting for dead-code candidates.
- **Gemini 3.8 Flash (High) through `agy`, in read-only plan mode, for grunt work.** It produced concept-duplication inventories for the backend and frontend and a classification of broad `except` handlers. I checked its decisive claims against the source. Two of its claims were false (that `trace` has its own execution loop, and that `trainGuards` duplicates `expectPlainObject`) and are excluded.
- **Code reading.** I read the hotspots and every file:line cited below.

**Limits.** At about 183K backend and 102K frontend production lines, I read most hotspots rather than every line. Most findings are structural. The runtime claims were checked by reading call paths, not by running them (no probes or benchmarks were run). The severity labels are my judgement.

---

## Executive summary

The code is careful and defensive, and its failure semantics are explicit. The main problem is **accumulated machinery**: several generations of mechanisms for the same concern coexist, and a lot of bespoke infrastructure re-implements things that Polars, FastAPI, Pydantic or a hard memory cap already provide. The top items still open:

1. **The hand-maintained API contract is the top churn source.** `schemas.py`, `api/types.ts`, `types/guards.ts` and `api/client.ts` are the four most-changed files over the last three months. A Pydantic-to-TS generator already exists but covers only two pilots. (§5.1)
2. **About 12 project-root/pipeline-dir resolvers**, several keyed on process cwd, with different semantics. One of them silently falls back to cwd. (§1.4)
3. **Node config is an untyped `dict[str, Any]`.** The shared validator is strict for only 3 of 19 node types; the rest are validated piecemeal. (§5.4)

Of the strategic simplifications in §4, the one still open is folding the four overlapping reuse mechanisms (§4.7).

---

## 1. Defects and brittle behaviour worth fixing now

### 1.4 About 12 project-root / pipeline-dir resolvers — HIGH (brittleness)
They have different semantics, and several depend on process cwd:
- `_sandbox._get_project_root` captures cwd on first call as a global, with a `set_project_root` override.
- `_project.get_project_root` walks up for `haute.toml` plus git; this is the spec'd resolver.
- `_path_resolution.current_runtime_project_root` is a contextvar.
- `routes/_helpers.pipeline_dir` (`:92`) returns cwd **even when `haute.toml` is missing**, only logging an error. That contradicts pipeline-config's "not being inside a Haute project raises".
- `routes/input_cache._project_root` is `Path.cwd()`.
- `routes/_node_data_service.node_data_project_root` uses the sandbox root.
- Also: `_builders._configured_pipeline_dir`, `_project_storage.resolve_project_dir`, `modelling/_mlflow_settings._project_root_default` and `assistant/_config._normalise_project_root`.
- Identical copies: `executor._pipeline_dir` (`:145`) and `_cache._pipeline_dir` (`:1436`).
- **Fix.** Resolve one `ProjectContext` (root, pipeline file, config) once at CLI or server start and pass it explicitly. Delete the cwd fallbacks.

### 1.8 Smaller concrete defects — LOW-MED
- Path containment is implemented five times (§3.1). `routes/_helpers.validate_safe_path` (`:59`) uses `Path.is_relative_to` after `resolve`, and `_sandbox.validate_project_path` uses `normcase` plus `commonpath` after `resolve`. Their containment comparison is the same: both compare resolved paths, so `..` and symlinks are collapsed first, and `normcase` folds case only on Windows, where `Path` comparison is already case-insensitive. The case-variant bypass that `validate_project_path`'s docstring cites cannot happen for resolved paths. They differ in one guard: `validate_safe_path` refuses an absolute input that is lexically outside the project before resolving it (`:71`). No escape is known; the cost is duplication, and `validate_safe_path` raises `HTTPException` from a helper.

---

## 2. Dead and redundant code to delete

### 2.3 Unreferenced and test-only production code — LOW-MED
- **Zero references:**
  - `_execution_context.ensure_execution_context` (`:1868`)
  - `_rating._rating_table_materialises` (`:1465`)
  - `_chunked_writes.iter_slices` and `reads_only_memory` (`:1288`, `:155`)
  - `_polars_io_registry._callable_owner` (`:644`)
  - `_git_core._assert_not_protected` (`:564`; the guard is enforced through `_is_protected` at `:738`)
- **Test-only callers:** `load_per_port_cache` (44 test refs), `_data_points.resolve_point` (22), `_model_scorer._compute_schema_hash`, `validate_submodel_instances`, `_config_io.remove_config_file`, `find_config_by_func_name` (the expression-parsing spec still lists it as a dependency), `_ram_estimate._parquet_metadata`, `_resolve_edge_join_column_names`, `_resolve_target_column_names`, `wrap_path_case_audit`, and the registry's `get_exec`/`get_codegen`.
- There are 25 `*_for_tests` / `_reset_*` / `_clear_*` hooks in production modules, plus `ExecutionFaultPoint` fault-injection points threaded through `ExecutionContext`.
- `routes/_train_service.py` is a "compatibility facade" that re-exports underscore-private names from five modules, mainly so tests can patch old paths.
- **Frontend (knip):**
  - 8 unused files, including the `panels/editors/banding/index.ts` and `rating/index.ts` barrels.
  - The whole `panels/editors/index.ts` barrel of 22 editors is unused; consumers go through `LazyNodeEditors`.
  - 33 unused exports, including 17 in `glmTerms.ts` and 11 in `traceStoryView.ts`.
  - 91 unused exported types.
  - One unlisted dependency (`@lezer/highlight`).

---

## 3. Duplication to consolidate

### 3.1 Backend
- **Error translation.** `_memory_limit_http_exception` is defined three times (`routes/pipeline.py:397`, `_training_preparation.py:90`, `_optimiser_service.py:430`), and `HTTPException.detail` is turned into a job failure record twice (`_optimiser_service.py:495`, `_training_preparation.py:102`).
- **Transport concerns in the domain layer.** `HTTPException` is raised from service modules: `_optimiser_service` 67 times, `_save_pipeline` 40, `_training_lifecycle` 35, `_training_preparation` 15, and `_job_store.require_job`. Background threads raise it too (`_optimiser_service._execute_pipeline :4798-4857` both records a job failure and raises HTTP 500), and `assistant/_tools.py:135` unwraps `HTTPException.detail` coming from the save service. Raise typed domain errors and map them at the edge.
- **Three subprocess-worker mechanisms with parallel error taxonomies.**
  - `_worker_isolation.run_isolated_worker`: one-shot, 11 `IsolatedWorker*Error` classes.
  - `_interactive_workers.InteractiveWorkerPool`: warm pool, 8 `InteractiveWorker*Error` classes mirroring Start/Timeout/Stopped/MemoryLimit/Crashed/Remote.
  - `_worker_protocol.run_worker_protocol`: versioned job transport with its own errors.
  - Plus `routes/_isolated_worker_async.py`, `IsolatedJobSupervisor` and `routes/_timeouts.py`.
  - Use one worker primitive (a pool with `max_uses=1` for one-shot work) and one error family.
- **Path containment.** There are five implementations: `validate_safe_path`, `validate_project_path`, `_artifact_paths.safe_path`, the `_json_shred/_publication` lstat/junction checks, and `_save_pipeline._validate_output_rel_path`.

### 3.2 Frontend
- **`useNodeResultsStore.ts` (1,568 lines)** mixes the store with 4 recency LRUs, module-level derived caches, validation and optimiser domain derivation.

### 3.3 Across the stack
- **Transient editor state stored inside node config.** Examples are `_columns` (470 frontend references), `_nodeId` (139), `_schemaWarnings`, `_availableColumns`, `_steps_error`, `_steps_discarded` and `_discarded_sidecar`. It is stripped out again by exclusion lists in `hashConfig` (`useNodeResultsStore.ts:326`), the backend cache-field classification, the sidecar allowlist and codegen. Give editor state its own channel beside `config`.

---

## 4. Over-engineered areas: strategic simplifications (these need a decision)

### 4.7 Four overlapping reuse mechanisms — MED
The four are:
- node-level instances (`@pipeline.instance`, `instanceOf` plus `inputMapping`)
- submodel definitions with instance occurrences (read-only copies, port minting with `_2`/`_3` suffixes)
- `inputMapping` on non-instance Polars transforms, to preserve logical names across rewires
- project `utility/` modules

Together they account for 258 `instanceOf`/`instance_of` references across source and frontend. Submodel-named files hold 6.3K source lines and 11.7K test lines, and 25 of the 48 submodel commits since May are fixes or hardening. A node instance is a one-node submodel instance, and folding the two together removes a whole identity and aliasing dimension.

### 4.10 Other heavy-but-reasoned designs to keep an eye on — LOW
- **Node registry.** `NODE_REGISTRY` covers only exec, codegen, contract and a few flags. `NodeType.RATING_STEP` is special-cased in 17 backend modules. A fuller `NodeSpec` (config model, builder, template, contract, projection transfer, trace enricher) would localise changes.
- **Cache identity framework** (`_cache.py`, 1,639 lines). It has 9 consumer contracts and per-node-type field classification. Once editor state is out of `config` (§3.3), hashing the whole canonical config becomes enough.

---

## 5. Weak areas with a stronger standard solution

### 5.1 Generate the whole frontend contract — HIGH
- Today three mechanisms coexist:
  - **Generated:** Pydantic → JSON Schema → TS types and validators, for two pilots only (`scripts/generate_api_contracts.py`, `frontend/src/generated`).
  - **Handwritten:** 233 `parse*` guards in `types/guards.ts` (4,073 lines), plus `trainGuards.ts` (1,533) and `api/types.ts` (2,204), mirroring `schemas.py` (3,973 lines, 284 classes).
  - **Fixture parity tests:** `tests/fixtures/ui_contracts/*.json`.
- These four files are the top churn set: `schemas.py` has 74 commits in 3 months, `api/types.ts` 69, `guards.ts` 61 and `client.ts` 55.
- FastAPI already emits OpenAPI. Extend the existing generator, or use `openapi-typescript` with a validator generator, to cover every response model. Keep handwritten code only for cross-field semantics that genuinely belong in the UI.

### 5.4 Typed node configs — HIGH
- `NodeData.config` is `dict[str, Any]` (`_types.py:1012`).
- `validate_node_config` (`_config_validation.py:377`) is strict only for Data Input, Data Output and Banding.
- Every other type is checked by scattered hand-written validators: in the save service, `_optimiser_service._validate_config` (`:4628`), `_training_lifecycle._validate_config` (`:1139`), the recovery validators and the runtime builders.
- Per-node Pydantic models, discriminated by `nodeType`, would give one validation boundary and one source for the generated frontend types (§5.1).

---

## 6. Specs, docs, policy and governance

- **Spec drift found:** `server-api`'s `pipeline_dir` cwd fallback versus pipeline-config's "not being inside a project raises".

---

## 7. Tests

- **Size and shape.** Backend tests total 421K lines (2.3× the source); `test_optimiser_routes.py` alone is 16,581 lines. According to the saved project notes, a full serial run takes about 40 minutes.
- **About 20K lines sit in files named after coverage campaigns or fix waves** rather than behaviour: `test_expression_parser_coverage` (3,166), `test_train_service_coverage` (2,556), `test_algorithms_coverage` (2,349), `test_json_cache_coverage_uplift`, `test_coverage_gaps`, `test_dry_fixes`, `test_*_w3_fixes`, `test_trace_w4_fixes` and `*_coverage` ×8. The frontend has `apiInputBundle3b`/`3c` tests. This is the typical result of a 100%-changed-line coverage gate. It makes tests hard to find by feature and rewards line-shaped assertions.
- **Suggestion.** Reorganise the tests by component and behaviour. Keep the mutation and critical-file ratchets for the genuinely safety-critical code (rating, deploy scoring, feature contracts), and relax the changed-line gate to risk-based coverage elsewhere.

---

## 8. Where each finding is tracked

Every open finding is a package in its owning component roadmap. This report
owns no work; the packages carry the plan, acceptance and evidence.

| Finding | Package |
|---|---|
| §1.4 Project-root resolvers | `PCFG-R04` ([pipeline config](pipeline-config.md)) |
| §1.8 and §3.1 Path containment ×5 | `SBX-R01` ([sandbox security](sandbox-security.md)) |
| §2.3 Unreferenced and test-only code | `ENGQ-R01` ([engineering quality](engineering-quality.md)) |
| §3.1 Three worker mechanisms | `ROAD-WORKER-05` ([background jobs](background-jobs-api.md)) |
| §3.2 Results store | `FSH-R03` ([frontend shared](frontend-shared.md)) |
| §3.3 Editor state inside node config | `PCFG-R08` ([pipeline config](pipeline-config.md)) |
| §4.7 Four reuse mechanisms | `SUB-R01` ([submodels](submodels.md)) |
| §4.10 Node registry | `PCFG-R09` ([pipeline config](pipeline-config.md)) |
| §4.10 Cache identity framework | `CACHE-S25` ([caching](caching.md)) |
| §5.1 Generated browser contract | `API-R03` ([server API](server-api.md)) |
| §5.4 Typed node configs | `PCFG-R07` ([pipeline config](pipeline-config.md)) |
| §6 Specification drift: the `pipeline_dir` cwd fallback | `PCFG-R04` ([pipeline config](pipeline-config.md)) |
| §7 Test organisation | `ENGQ-R05` ([engineering quality](engineering-quality.md)) |
