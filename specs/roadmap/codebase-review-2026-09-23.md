# Haute codebase review — 23 September 2026

Reviewed at `main` @ `9319b11d`. The review read the code and changed none of it. This is a dated supporting report: its findings are tracked as packages in the component roadmaps listed in [section 8](#8-where-each-finding-is-tracked), and those packages, not this report, carry the plans.

## How the review was done

- **Specs first.** I read the high-level spec of every component (the execution engine, caching, IO, pipeline-config, codegen, expression-parsing, server-api, tracing, JSON shredding, background jobs, sandbox, modelling, optimiser, deploy and engineering-quality specs in full; the rest via their Purpose and Design-rationale sections), the specs README, the roadmap index and the PR #227 review documents, so that tracked work is not reported as new.
- **Mechanical metrics.** radon cyclomatic complexity, jscpd clone detection (Python and TS, without size limits), vulture (Python dead code), knip (frontend dead exports), 3-month git churn, and reference counting for dead-code candidates.
- **Gemini 3.8 Flash (High) through `agy`, in read-only plan mode, for grunt work.** It produced concept-duplication inventories for the backend and frontend and a classification of broad `except` handlers. I checked its decisive claims against the source. Two of its claims were false (that `trace` has its own execution loop, and that `trainGuards` duplicates `expectPlainObject`) and are excluded.
- **Code reading.** I read the hotspots and every file:line cited below.

**Limits.** At about 183K backend and 102K frontend production lines, I read most hotspots rather than every line. Most findings are structural. The runtime claims were checked by reading call paths, not by running them (no probes or benchmarks were run). The severity labels are my judgement.

**Size.** The backend is 182.5K lines (`src/haute`), the frontend 102K production lines. Tests are 421K backend lines (2.3× the source) and 144K frontend lines, and the specs are 41K lines. Radon counts 232 functions with cyclomatic complexity ≥20 and 27 with ≥50. The largest are `_execute_eager_core` (977 lines, CC 191) and `_execute_lazy` (1,062 lines, CC 122). Copy-paste duplication is low (0.6% Python, 1.6% TS), so the duplication problems are mostly **conceptual**: the same idea is implemented several times in different shapes.

---

## Executive summary

The code is careful and defensive, and its failure semantics are explicit. The main problem is **accumulated machinery**: several generations of mechanisms for the same concern coexist, and a lot of bespoke infrastructure re-implements things that Polars, FastAPI, Pydantic or a hard memory cap already provide. The top items:

1. **A process-global lock is held for the whole of an optimiser solve.** `temporary_streaming_chunk_size` holds a module-level RLock across whatever runs inside it. The optimiser runs setup, auto-range and the solve on server threads inside that scope, so the estimate endpoint and the other optimiser jobs block until the solve finishes. (§1.1)
2. **Deploy does not ship `utility/` modules.** `haute init` scaffolds them for the preamble to import, but the container and MLflow bundles omit them. Local validation passes, and the served model fails on import. (§1.2)
3. **The user docs describe node types that no longer exist** (Data Source/Data Sink, `sourceType: "flat_file"`), and there is no Data Input/Output page. Meanwhile about 5K lines of tests (plus a 1.4K-line coverage ledger) police the *internal* spec corpus. (§1.3, §6)
4. **`DataFrameExecutionCache` is dead in production.** About 900 source lines and about 3K test lines remain, and two specs still describe it as live. (§2.1)
5. **The 2,251-line chunked map-reduce engine serves one feature**, the frontier auto-range estimate. That feature is itself a 300-line duplicate of the non-streaming job, and it computes what one streaming `group_by` could compute, if that query can be shown to keep the specified memory bound. (§2.2)
6. **The hand-maintained API contract is the top churn source.** `schemas.py`, `api/types.ts`, `types/guards.ts` and `api/client.ts` are the four most-changed files over the last three months. A Pydantic-to-TS generator already exists but covers only two pilots. (§5.1)
7. **Two independent memory-safety systems.** A static analyser of user Polars code (lineage, projection, cardinality, RAM estimation, operator registry; about 16K lines plus about 30K lines of tests) sits alongside the hard-capped killable workers that already bound almost every heavy path. (§4.1)
8. **Two ~1,000-line execution cores** (eager and lazy) duplicate their prologue. Preview is now "lazy plus collect the target", so they could be one walker with a collect policy. (§4.2)
9. **About 12 project-root/pipeline-dir resolvers**, several keyed on process cwd, with different semantics. One of them silently falls back to cwd. (§1.4)
10. **Node config is an untyped `dict[str, Any]`.** The shared validator is strict for only 3 of 19 node types (the rest are validated piecemeal), and sidecar writes silently drop unknown keys. (§1.5, §5.4)

Several strategic simplifications need a product decision rather than a refactor. They are listed in §4 with their trade-offs: the hand-written Polars expression interpreter, value-matching trace correlation, the recovery/repair subsystem, the four overlapping reuse mechanisms, GYO-based output assembly, and the "accident guard" sandbox.

---

## 1. Defects and brittle behaviour worth fixing now

### 1.1 Process-global Polars-config lock held across long work — HIGH
- `src/haute/_polars_utils.py:688` `temporary_streaming_chunk_size` takes `_STREAMING_CHUNK_SIZE_LOCK` (a module RLock, `:31`) for the entire `with` body, then restores *all* Polars config via `pl.Config.load`.
- The optimiser runs on daemon threads **in the server process**: setup at `routes/_optimiser_service.py:3210`, auto-range at `:4602`, the solve at `:5646`, and the frontier sweep at `routes/optimiser.py:1810`. It wraps whole pipeline execution (`:4729`), auto-range (`:2137`, `:4372`) and the solve itself (`:5488`, which encloses `_solve_ratebook`/`_solve_online`) in that scope.
- **Effect.** `POST /api/optimiser/estimate` (a sync route, `routes/optimiser.py:1318`, which enters the scope through `_execute_pipeline` and again at `:360`), the "non-blocking" auto-range jobs, and other optimiser setups in the same process wait until a running solve releases the lock. Preview and trace take the same scope in the server process when interactive execution runs on threads (`routes/pipeline.py:1281`, `:1463`). This is a hidden global serialisation point, and it contradicts the optimiser spec's "estimates … do not reserve that global slot".
- **Fix.** Set the chunk size once per process at start-up and drop the per-request value (§2.4). The lock cannot simply be narrowed: Polars exposes the setting only as process-global configuration (the `POLARS_STREAMING_CHUNK_SIZE` environment variable), and the chunked writer sizes its slices from the ambient value, so overlapping scopes with different values would run with each other's setting and restore the wrong one.

### 1.2 Deployed pipelines cannot import project `utility/` modules — HIGH
- `haute init` writes `utility/__init__.py` and `utility/features.py` ("These are imported into main.py"; `cli/_init_cmd.py:536-537`, `_scaffold.py:958-968`), and the editor has utility CRUD routes.
- The container Dockerfile copies only `deploy_manifest.json`, `app.py` and `artifacts/` (`deploy/_container.py:874-877`). The Databricks pyfunc path passes no `code_paths` (there is no match anywhere in `deploy/`). The scorer compiles the preamble (`deploy/_scorer.py:1032`), so `from utility.x import *` raises `ModuleNotFoundError` in the served image.
- `validate_deploy` and golden test-quote scoring run in the project cwd, where `utility` *is* importable, so the gate goes green and the failure surfaces only in production. The deploy spec records the limitation, but nothing enforces it.
- **Fix.** Bundle `utility/` (and any other project package the preamble imports) into both targets. Alternatively, reject project-local preamble imports at `validate_deploy`, and run the dry-run in a clean cwd.

### 1.3 The published node reference documents removed node types — HIGH (user-facing)
- `docs/building-models/nodes/data-source.md` and `data-sink.md` are in `mkdocs.yml:122,140` and document `sourceType: "flat_file"` configs, which the canonical-only code now rejects. No Data Input or Data Output page exists; only `docs/index.md` and `optimiser.md` mention Data Input.

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

### 1.5 Sidecar writes silently discard unknown config keys — MED-HIGH
- `_config_io.py:127-141` drops every key missing from the node's TypedDict and records it only as a server log warning (`config_keys_dropped_at_write`). A UI field added without its TypedDict entry vanishes on save with no user feedback. That breaks the AGENTS "no silent fallbacks" rule and the canonical-only policy (under which non-canonical input is invalid, not silently rewritten).
- The opposite policy exists next door: Explore display validators *preserve* unknown keys so "a newer UI can round-trip through an older parser" (`_explore_overview.py:43`), which is a compatibility promise the README disclaims.

### 1.6 Frontier point → solve summary is derived twice, and the two copies disagree — MED
- The backend has `routes/optimiser.py:627` `_frontier_point_result_dict`; the frontend has `stores/useNodeResultsStore.ts:587` `deriveSolveResultForFrontierPoint`. They differ:
  - The frontend accepts two lambda shapes (nested `lambdas` or flat `lambda_*`).
  - The frontend silently falls back to the original `converged`, `baseline_objective` and `baseline_constraints` when a field is absent, whereas the backend returns 400 without `converged` and always takes the baseline from the base result.
  - Only the backend drops `scenario_value_histogram` when stats are present.
  - The non-converged warning string is duplicated in both languages.
- **Fix.** Make selection server-authoritative and delete the client derivation.

### 1.7 Swallowed exceptions that contradict "fail loud" — MED
A grep finds 467 broad handlers (`except Exception`/`BaseException`/bare `except`/`suppress`). Gemini classified them and I spot-checked the classification; its category counts cover 464 of the 467, so three were left unclassified. It found 118 re-raise, 182 convert to a typed error, 63 cleanup, 66 log-and-continue, 33 return a default and 2 silent. Most of the log-and-continue and default cases are deliberate and specified: per-step trace enrichment, CLI smoke/impact reporting, cleanup paths, and watcher resilience. Three are not:
- `_mlflow_io.py:554-557` `_catboost_offset_column` returns `None` on **any** exception from `model.get_metadata()`. The model is then treated as having no offset and scores from baseline 0, which is exactly the "silently score from baseline 0" outcome its own docstring warns about. Only an absent key should mean "no offset"; any error should propagate.
- `routes/modelling.py:253-259` `estimate_training` catches any exception from the RAM estimator and returns an empty `TrainEstimateResponse()` with HTTP 200. An estimator bug becomes "unknown" in the UI, indistinguishable from the specified "estimate unavailable".
- `routes/optimiser.py:1333-1340` `estimate_solve` does the same for the source row count.

### 1.8 Smaller concrete defects — LOW-MED
- `panels/editors/_InputSnapshotCacheButton.tsx:40` `pollJobToTerminal` is an unbounded `for (;;)` poll every 800 ms, with no abort signal or timeout. It keeps polling after unmount.
- Non-finite floats reach JSON in three incompatible encodings:
  - `_json_safe.to_json_safe` produces a tagged object `{"__haute_type__": "non_finite_float", …}`.
  - `_column_summary.json_safe_scalar` (`:55`) produces the strings `"nan"`/`"inf"`.
  - `_optimiser_apply_explainability._json_safe` (`:724`) produces `null`.
- `_io._normalise_dtype` (`:103`) falls back to `getattr(pl, dtype_name)`, so any Polars attribute name is accepted as a dtype. Use `_polars_dtypes.parse_dtype`.
- `_polars_utils.atomic_write` (`:826`) stages to a fixed `dest.with_suffix(".parquet.tmp")`, which collides between concurrent writers and is the wrong suffix for CSV, and it does no fsync. `_file_ops.atomic_write_bytes` already does this correctly.
- Path containment is implemented five times (§3.1). `routes/_helpers.validate_safe_path` (`:59`) uses `Path.is_relative_to` after `resolve`, and `_sandbox.validate_project_path` uses `normcase` plus `commonpath` after `resolve`. Their containment comparison is the same: both compare resolved paths, so `..` and symlinks are collapsed first, and `normcase` folds case only on Windows, where `Path` comparison is already case-insensitive. The case-variant bypass that `validate_project_path`'s docstring cites cannot happen for resolved paths. They differ in one guard: `validate_safe_path` refuses an absolute input that is lexically outside the project before resolving it (`:71`). No escape is known; the cost is duplication, and `validate_safe_path` raises `HTTPException` from a helper.
- The client picks the snapshot build profile by string-matching server error text: `hooks/ensureInputSnapshots.ts:245-262` retries with `preview_eager` when `detail.startsWith("snapshot_build_unsupported")`. The server already has automatic input preparation and should choose the profile itself.

---

## 2. Dead and redundant code to delete

### 2.1 `DataFrameExecutionCache` — HIGH, easy
- `execution.build_dataframe_execution_cache_request` (`:1452`) has **no production caller**, so `default_dataframe_execution_cache` is unreachable in production and `_execute_lazy(dataframe_cache_request=…)` is passed only by tests. Deploy tests even assert it is `None` (`tests/test_deploy_internals.py:1521,1694,4057`). `assistant/_assets.py:551,629` invalidates a cache that is never populated.
- **Removable:** `_dataframe_execution_cache.py` (685 lines; move `_upstream_subgraph`, which `_data_points.py:232` uses), roughly 150 lines of cache-request branches and mutual-exclusion checks in `_execute_lazy.py:1303-1456`, the cache-key and policy-fingerprint helpers in `execution.py`, and `tests/test_dataframe_execution_cache.py` (2,007) plus `tests/test_execute_lazy_dataframe_cache.py` (1,057).
- **Spec drift to fix at the same time:** the caching high-level spec ("The dataframe execution cache stores validated Parquet artifacts…") and the execution-engine spec ("only a caller's dataframe-cache request (deploy scoring) materialises").

### 2.2 The chunked map-reduce engine and the streaming auto-range path — HIGH
- `chunking.py` (2,251 lines, plus about 3.8K lines of chunk tests including hypothesis "whitelist proofs") has exactly one production consumer: `_run_streaming_frontier_auto_range_job` (`routes/_optimiser_service.py:4240`, 308 lines). That function is largely a clone of `_run_frontier_auto_range_job` (`:3989`, 250 lines); jscpd finds 72- and 34-line clones at `4426-4530` vs `4117-4221`.
- Both feed `_ScenarioFrontierRangeAccumulator` (`:1921`), a hand-rolled out-of-core hash-partitioned `group_by` that writes per-bucket Parquet parts.
- The quantity computed is the sum over quotes of each quote's min/max per constraint: `group_by(quote).agg(min, max).select(sum)`, one streaming query. The estimate endpoint runs a similar per-quote `group_by` on the same frame (`routes/optimiser.py:350-372`), but it selects only the quote-id column, so projection can skip the scoring nodes; it says nothing about the memory needed to score and reduce the constraint columns.
- **The catch.** The optimiser spec promises that, when the upstream chain is provably row-local, auto-range runs chunk by chunk and never materialises the fully expanded scenario frame; the chunk plan exists to chunk *before* scenario expansion. One streaming `group_by` keeps that bound only if scenario expansion and model scoring stream in Polars and the per-quote state fits in memory.
- **Measure first.** On a fixture with high quote cardinality, scenario expansion and model scoring, compare peak memory of the chunked path and of one streaming `group_by`. If the `group_by` stays within the bound, delete the planner, runner, capability declarations, streaming plan (`_StreamingAutoRangePlan`, `_ChunkFallback`, `_build_streaming_auto_range_plan`) and the duplicate job, keeping `classify_chunk_local_polars_code` if the row-locality check is still wanted (`_execute_lazy.py:67`, `_trace_correlation.py:1738`). If not, merge only the duplicated job code.

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
- `trace.PreviewReader` plus the three-shape duck typing in `_resolve_preview_snapshot` was built for a "future Redis-backed reader". The tracing spec itself says HTTP preview entries never share a key with trace, so the first trace after a preview is always cold.
- **Frontend (knip):**
  - 8 unused files, including the `panels/editors/banding/index.ts` and `rating/index.ts` barrels.
  - The whole `panels/editors/index.ts` barrel of 22 editors is unused; consumers go through `LazyNodeEditors`.
  - 33 unused exports, including 17 in `glmTerms.ts` and 11 in `traceStoryView.ts`.
  - 91 unused exported types.
  - One unlisted dependency (`@lezer/highlight`).

### 2.4 The per-request `streaming_chunk_size` knob — MED
It is a Polars engine tuning parameter, exposed as a user setting (`PipelineSettingsModal.tsx:223`) and sent on every request: 13 request models in `schemas.py`, `client.ts:839,926,1042`. It is threaded through every route and service, applied through the global-lock config swap in §1.1, and backed by a 1,617-line test file (`tests/test_streaming_chunk_size_threading.py`). **Make it a process or worker start-up setting (`haute.toml` or env), or drop it.**

### 2.5 Parallel legacy representations in the assistant — MED
`assistant/_catalog.py` keeps a "legacy catalogue" beside the "authoritative" capability manifest (`:267`, and `_tools.py:709` "legacy node-catalogue compatibility view"). It also keeps legacy single-file examples beside the manifest bundles (`_assets.py:135-150`). The self-test (795 lines) and provider-qualification (796 lines) harnesses ship inside the runtime package.

### 2.6 Repository hygiene — LOW
- Tracked `node_modules/.vite/vitest/<hash>/results.json` (a build cache at the repo root).
- Tracked `mlflow.db` (612 KB).
- `repro/` benchmarks.
- The non-runnable `rating/` reference project, which is missing `nest_example.json` and `config/expander/premium.json`, yet the root `haute.toml` selects it as the repository's pipeline.
- PR-227 benchmark `.py`/`.json`/`.test.tsx` files living under `specs/roadmap/`.
- The output assembler's normative algorithm document is **outside the repo**: `_output_assembler.py` cites `notes-haute → OUTPUT_ASSEMBLY_PROPERTIES.md`.

---

## 3. Duplication to consolidate

### 3.1 Backend
- **Error translation.**
  - No FastAPI exception handler is registered (`exception_handler` has zero matches). Instead there are 53 route-level `except Exception: log; raise HTTPException(500, _INTERNAL_ERROR_DETAIL)` blocks (29 nearly identical ones in `routes/git.py`) and 88 `_INTERNAL_ERROR_DETAIL` references.
  - `_memory_limit_http_exception` is defined three times (`routes/pipeline.py:397`, `_training_preparation.py:90`, `_optimiser_service.py:430`), and `HTTPException.detail` is turned into a job failure record twice (`_optimiser_service.py:495`, `_training_preparation.py:102`).
  - **Register handlers for `Exception`, the `HauteError` families and the public contract errors once.**
- **Transport concerns in the domain layer.** `HTTPException` is raised from service modules: `_optimiser_service` 67 times, `_save_pipeline` 40, `_training_lifecycle` 35, `_training_preparation` 15, and `_job_store.require_job`. Background threads raise it too (`_optimiser_service._execute_pipeline :4798-4857` both records a job failure and raises HTTP 500), and `assistant/_tools.py:135` unwraps `HTTPException.detail` coming from the save service. Raise typed domain errors and map them at the edge.
- **Three subprocess-worker mechanisms with parallel error taxonomies.**
  - `_worker_isolation.run_isolated_worker`: one-shot, 11 `IsolatedWorker*Error` classes.
  - `_interactive_workers.InteractiveWorkerPool`: warm pool, 8 `InteractiveWorker*Error` classes mirroring Start/Timeout/Stopped/MemoryLimit/Crashed/Remote.
  - `_worker_protocol.run_worker_protocol`: versioned job transport with its own errors.
  - Plus `routes/_isolated_worker_async.py`, `IsolatedJobSupervisor` and `routes/_timeouts.py`.
  - Use one worker primitive (a pool with `max_uses=1` for one-shot work) and one error family.
- **Process memory measurement.** RSS reading is implemented four times, each with its own Windows `ctypes` `PROCESS_MEMORY_COUNTERS` struct and `/proc` parsing: `_execution_context.py:739-790`, `_process_memory.py:58-100` (a 22-line clone), `_native_memory_limit.py:430-481` and `modelling/_algorithms.py:51-95`. On top of that sits `_host_memory.py` (743 lines). One module, or `psutil`, would do.
- **Artifact stores and freshness proofs.**
  - `SourceCacheStore` plus node snapshots (about 2.8K lines) and the JSON API-input cache (about 3.3K lines: `_json_shred/_cache`, `_runtime_storage`, `_publication`, `_source_proof`) each implement staging, publication, locks, owner liveness and disk budgets separately.
  - They also use three different freshness policies: Windows USN/native change tokens plus a full SHA-256 for JSON sources, signature plus a (mtime, size, digest) memo for snapshots, and a bare (mtime, size) check in `StatGatedCache`.
  - **CACHE-S08 (decided 22 Sep) already folds the JSON cache into the snapshot store.** When doing it, also settle on one freshness policy.
- **File-lock wrappers.** There are three, each with its own timeout and stale-lock loop (`_project_mutation_lock`, `_source_cache._StoreFileLock`, `_json_shred/_publication._CacheBuildLock`). There are also three hand-rolled `OrderedDict` LRUs beside `LRUCache` (`StatGatedCache`, `_VerifiedRuntimeSnapshotCache`, `_DataFileSignatureMemo`).
- **Graph traversal.** Ancestor walks are implemented five times (`_topo.ancestors`, `_graph_utils.upstream_node_ids`, `_trace_waterfall._has_lineage_path`, `routes/pipeline._recovery_ancestor_ids`, `_optimiser_service._upstream_slice_contains_node_type`). `projection._canonical_topological_ranks` hand-rolls Kahn's algorithm with a heap while `_topo` uses `graphlib`. `executor._preview_preparation_order` (`:977`) and `trace._trace_preparation_order` (`:367`) have identical bodies.
- **dtype ↔ string mapping.** It lives in about 7 places: `_polars_dtypes` (the canonical one), `_io._normalise_dtype` (own alias table), `_rating` (own descriptor format), `_output_assembler._document_dtype`, `deploy/_scorer._canonical_dtype`, `modelling/_signature._map_dtype` and `_training_job._polars_dtype_name`.
- **Path containment.** There are five implementations: `validate_safe_path`, `validate_project_path`, `_artifact_paths.safe_path`, the `_json_shred/_publication` lstat/junction checks, and `_save_pipeline._validate_output_rel_path`.
- **Duplicated validation for legacy modelling fields.** The `split`/`cross_validation` check exists in both `modelling/_train_config.py:553` and `routes/_training_lifecycle.py:1186`.
- **Git access outside the chokepoint.** `deploy/_container._git_sha_short` shells out to `git` directly despite the "one chokepoint per tool" rule, and `_project._has_git` differs from `_git_core._is_git_repo`.

### 3.2 Frontend
- **Polling.** A shared `jobPollingController`/`useJobPolling` exists, but there are 16 polling implementations, with ad hoc loops in `CacheFetchButton.tsx:141`, `ensureInputSnapshots.ts`, `_InputSnapshotCacheButton.tsx:40`, `useOptimiserAutoRange.ts:178` and `api/dispersion.ts:117`.
- **Error-message extraction.** There are 27 `instanceof ApiError` sites. Identical helpers exist in `useMlflowBrowser.ts:161` and `_DatabricksSelector.tsx:165`, `usePipelineAPI.ts:401` and `useTracing.ts:216`, and `OptimiserConfig.tsx:84` and `useOptimiserAutoRange.ts:153`, plus `gitError.ts`. One helper would do.
- **Copy-paste blocks.**
  - `ApiInputEditor.tsx:1391-1565` vs `OutputEditor.tsx:1362-1452` (175 lines).
  - `GLMTargetConfig` vs `TargetAndTaskConfig` (75 lines).
  - The modelling chart tabs (Lift, Residuals, AvE, PDP, Loss) share 60–90-line blocks.
  - `DataInputEditor` vs `DataOutputEditor`.
  - `useBandingStats` vs `useRatingLevels`.
  - `IdentityPromptModal`, `WorkingBranchModal` and `DivergenceModal`.
  - About 135 lines of internal clones in `useGraphStore`.
- **Smaller repeats.**
  - 37 formatting helpers; byte formatting alone is implemented four times.
  - Debounce is implemented ad hoc 8 times.
  - 22 hand-built tables while the `SimpleTable` helper goes mostly unused.
  - ECharts is used for one chart, and every other chart is hand-rolled SVG (`ConvergenceChart` re-implements the axis scaling in `chartHelpers`).
  - `ReadOnlyNodeConfig` duplicates the `NodeConfigEditor` per-type dispatch switch.
  - Guard helpers `asRecord` and `isRecord` re-implement `expectObject`.
- **`useNodeResultsStore.ts` (1,568 lines)** mixes the store with 4 recency LRUs, module-level derived caches, validation and optimiser domain derivation (see §1.6).

### 3.3 Across the stack
- **Semantic invariants re-validated in three places.** Tuning and evaluation artifacts are strictly reloaded by modelling, re-checked by the response models (`schemas.py` `TuningReportPayload._validate_report`, CC 53, 138 lines, and `EvaluationReportPayload._validate_report`, CC 36), and checked again in the browser (`trainGuards.ts:537`, `:867-883`: "trials must be contiguous", "start with one empty baseline"). Validate once, where the data is produced.
- **Transient editor state stored inside node config.** Examples are `_columns` (470 frontend references), `_nodeId` (139), `_schemaWarnings`, `_availableColumns`, `_steps_error`, `_steps_discarded` and `_discarded_sidecar`. It is stripped out again by exclusion lists in `hashConfig` (`useNodeResultsStore.ts:326`), the backend cache-field classification, the sidecar allowlist and codegen. Give editor state its own channel beside `config`.

---

## 4. Over-engineered areas: strategic simplifications (these need a decision)

### 4.1 Static memory analysis versus hard caps — HIGH
- **The stack (about 16K source lines plus about 30K test lines).** `_column_lineage` (3,576 lines, a closed-vocabulary interpreter of user Polars code), `projection` (5,045), `_ram_estimate` (2,296), `_polars_operations` (1,508, per-operator measured memory factors), `chunking` (2,251), `_execution_admission`, `_cardinality`, `_polars_selectors` and `_estimate_calibration`. It exists to prove column demand at capture points, estimate RAM for admission, and prove chunk safety.
- **The overlap.** Nearly every heavy surface already runs in a killable spawn worker under a native hard cap (Job Object, cgroup or `RLIMIT_AS`). The spec already downgrades an unavailable estimate under a cap to "warned, run conservatively". The static estimate is essential only where no cap exists: the optimiser (§4.3), Databricks serving and macOS.
- **The cost.**
  - The analyser fails closed on any unregistered Polars method.
  - It has to track Polars' behaviour release by release (the notes on `shift` changing in 1.44 are an example).
  - `_chunked_writes` inspects Polars' internal IR (`_SUPPORTED_IR_MAJOR = 14`); the Fable 5.1 review already flagged that a Polars upgrade silently turns every sliced write into a native one.
- **Option.** Make the hard-capped worker the single safety mechanism. Keep only a coarse Parquet-footer size warning.
- **The one real trade-off: capture width.** One option is to write capture snapshots at the full width that reaches them. Parquet projection on read still narrows downstream reads, and the widening/`partial`/supersession machinery in `_node_snapshots` and `_seed_plans` goes away. Measure the extra disk and write time on your widest real sources before deciding.

### 4.2 One execution walker instead of eager, lazy, trace and score cores — HIGH
- `_execute_lazy` (1,062 lines, 18 parameters, 11 nested functions including a 214-line `_build_lazy_node`) and `_execute_eager_core` (977 lines, CC 191) repeat the same prologue: prepare the execution, `_check_snapshot_plan`, projection planning, `_build_funcs` and `NodeBoundaryRunner`. On top of these sit `executor._execute_graph_core` (579 lines, 9 closures), `trace._execute_trace_core` (474), `deploy._score_graph_lazy` (442) and `Pipeline.run`/`score`, which has its own topological loop.
- Preview is now "target-only": ancestors stay lazy and only the target is collected. **Eager is therefore lazy plus a collect policy.** One walker that produces LazyFrames, with pluggable collect, capture and contract policies, would remove roughly half this code. This is also where the churn is concentrated (`_execute_lazy.py` has 54 commits in 3 months, `_builders.py` 49).

### 4.3 Isolate the optimiser pipeline before (or without) isolating the solver — HIGH
The optimiser's upstream pipeline execution and its solve run on server threads, with no hard cap and cooperative cancellation only. Every other heavy surface runs in hard-capped workers. ROAD-WORKER-04 (isolating the optimiser workflows) is deferred until solvers have versioned persistence.

A smaller step is available now. Run the **pipeline materialisation** in the existing hard-capped worker, as training preparation does, and write the projected scored Parquet there. The solver thread already builds the grid from a Parquet file (`build_grid_from_parquet_chunked`). This removes the in-process pipeline execution (and the §1.1 lock contention) without touching solver persistence.

### 4.4 A hand-written Polars interpreter for trace formulas — HIGH
`_expression_parser.py` (2,527 lines) contains `_ExprEvaluator` (656 lines; `_call` has CC 95) and `_BranchTrackingEvaluator`. They re-implement Polars null propagation, Kleene logic, division rules, i64 overflow and half-to-even rounding, backed by about 8K lines of tests including parity property suites. The spec admits that many methods return `None` (unsupported). The stated reason is "don't execute user code for a display question", but project code is now declared trusted (sandbox F1) and has already executed in the same run.

**Evaluate the extracted sub-expressions, and each `when`/`then` condition for branch highlighting, with Polars itself.** A row-local sub-expression can be evaluated on a one-row slice of the node's input frame, which is exact. A sub-expression that depends on other rows (aggregations, `over`, `shift`, `diff`, ranks, cumulative operations) cannot: `pl.col("x").sum().over("y")` over `x = [10, 20]` in one group is `30` for both rows but `10` and `20` row by row, as `test_window_semantics_diverge_from_single_row_approximation` records. Those need the full input frame the trace execution used, or an explicit "not computable from one row". Keep the AST only for pretty-printing the formula.

### 4.5 Trace row correlation by value matching — MED (design alternative)
`_trace_correlation.py` (2,355 lines) exists because trace rejects row-id injection. It contains `RowScopeResolver`, carried-value proofs, ambiguity diagnostics and edge-join suffix provenance. Trace already re-executes cold under its own plan. An internal row identity carried during trace runs would make filter, join and `with_columns` paths exact, with value matching needed only for aggregates. It cannot simply be a column injected at the sources, though: user code receives the frames themselves, so an extra column changes all-column selectors (`pl.sum_horizontal(pl.all())`), `unique()` without a subset and schema-inspecting code, and dropping it at the output cannot undo that. That would break trace's pure-observation contract. The identity has to be carried only where user code cannot see it.

### 4.6 Editor recovery and repair subsystem — MED
The subsystem is about 6.1K backend lines:
- `_pipeline_recovery` (2,316 lines; `_build_recovery_graph` is 368 lines at CC 77)
- `_pipeline_repair` (1,003) and `_pipeline_repair_actions` (858)
- `_node_config_recovery` (727; `_validator_issues` has CC 87)
- `_parser_regex` (894 lines, a second parser for syntax-invalid files)
- `_recovery_sources` and `_submodel_recovery`

On top of that come degraded and source-only document states, recovery previews, and plan-hash dry-run/apply repairs, plus frontend recovery views. This is the price of the ".py is the source of truth and may be hand-edited anywhere" premise. The same premise drives the stepped-transform reconcile described in the pipeline-config spec: render the steps, compare the result with the extracted body, and on any difference discard the steps behind `_steps_discarded`/`_discarded_sidecar` markers. A narrower contract would cover most of the value with a fraction of the code: users hand-edit only node bodies and the preamble, and anything else that breaks shows the parse error with an "open in editor" action.

### 4.7 Four overlapping reuse mechanisms — MED
The four are:
- node-level instances (`@pipeline.instance`, `instanceOf` plus `inputMapping`)
- submodel definitions with instance occurrences (read-only copies, port minting with `_2`/`_3` suffixes)
- `inputMapping` on non-instance Polars transforms, to preserve logical names across rewires
- project `utility/` modules

Together they account for 258 `instanceOf`/`instance_of` references across source and frontend. Submodel-named files hold 6.3K source lines and 11.7K test lines, and 25 of the 48 submodel commits since May are fixes or hardening. A node instance is a one-node submodel instance, and folding the two together removes a whole identity and aliasing dimension.

### 4.8 Output assembly by implicit joins — MED
`_output_assembler.py` treats "two tables carrying the same field" as a join constraint. That forces GYO α-acyclicity reduction, cyclic-core detection, recursive "surgical cut" planning and bag natural joins. An explicit nesting model, in which each array table names its parent table and key, makes cycles unrepresentable and the assembler a simple tree walk. The algorithm's normative document also lives outside this repository (§2.6).

### 4.9 A sandbox that is no longer a sandbox — MED
Since F1 declared project code trusted, the node-code AST denylist no longer serves a security purpose. It still blocks `class` definitions (`_sandbox.py:410`), `global`/`nonlocal` (`:416-419`) and `getattr`/`type`/`vars` (`:102-119`, `:228-234`), while `pl.io.csv.functions.os` reaches the OS anyway (the spec says so). That makes it pure friction for legitimate code. **Keep the exact pickle/joblib allowlist** (model artifacts are untrusted) and demote the node-code guard to a syntax check or lint.

### 4.10 Other heavy-but-reasoned designs to keep an eye on — LOW
- **Codegen.** Every node has two semantic implementations: a runtime closure in `_builders.py` and a source template in `_codegen_builders.py`. The config-folder rewrite then splices the decorator out by string slicing at `code.index("\ndef ")` (`codegen.py:300-321`), after the builders have already emitted the full config inline (`_gen_rating_step` emits `tables={…!r}`). This contradicts the codegen spec's "never splice a manually located delimiter". Emit `config=` directly.
- **Node registry.** `NODE_REGISTRY` covers only exec, codegen, contract and a few flags. `NodeType.RATING_STEP` is special-cased in 17 backend modules. A fuller `NodeSpec` (config model, builder, template, contract, projection transfer, trace enricher) would localise changes.
- **`ExecutionContext` is a 1,011-line class.** It combines cancellation, admission, RSS sampling, metrics, evidence, calibration, telemetry and fault points.
- **Cache identity framework** (`_cache.py`, 1,639 lines). It has 9 consumer contracts and per-node-type field classification. Once editor state is out of `config` (§3.3), hashing the whole canonical config becomes enough.
- **The git branch-pair model** (working and ledger branches, `commit-tree` milestones, trash tombstones, fork map, last-pushed SHAs; about 6K backend lines plus about 5K frontend lines).
- **Hosted storage** (full git bundles to UC volumes with claim leases and a create-only fence, which is still unproven against the provider).
- **The assistant** (14.6K lines).
- **Scaffolding for targets that don't exist yet.** `haute init` generates complete CI for 3 providers × 7 deploy targets. Three of those targets build and push an image and then raise `NotImplementedError` (`deploy/_container.py:288`), and two are rejected outright (`deploy/__init__.py:61`), yet their templates are still parsed and tested.
- **Persistent caches with no automatic eviction, by design.** Input snapshots and node outputs have no byte or count limits (the io-layer spec removed the former 20/40 GiB budgets). Every admitted preview captures its joins and materialising operations at full data. Disk growth is left to the user's cache inventory.
- **Process-global library state mutated per request under global locks.** This happens in three places: Polars config (§1.1), MLflow fluent tracking and registry URIs (`_mlflow_utils.mlflow_fluent_operation`, `_FLUENT_LOCK`), and `os.environ` switches (`runtime_environment_inference`). Prefer explicit clients and per-call options.
- These are defensible product choices, but each is a large surface for a prerelease product.

---

## 5. Weak areas with a stronger standard solution

### 5.1 Generate the whole frontend contract — HIGH
- Today three mechanisms coexist:
  - **Generated:** Pydantic → JSON Schema → TS types and validators, for two pilots only (`scripts/generate_api_contracts.py`, `frontend/src/generated`).
  - **Handwritten:** 233 `parse*` guards in `types/guards.ts` (4,073 lines), plus `trainGuards.ts` (1,533) and `api/types.ts` (2,204), mirroring `schemas.py` (3,973 lines, 284 classes).
  - **Fixture parity tests:** `tests/fixtures/ui_contracts/*.json`.
- These four files are the top churn set: `schemas.py` has 74 commits in 3 months, `api/types.ts` 69, `guards.ts` 61 and `client.ts` 55.
- FastAPI already emits OpenAPI. Extend the existing generator, or use `openapi-typescript` with a validator generator, to cover every response model. Keep handwritten code only for cross-field semantics that genuinely belong in the UI.

### 5.2 Use FastAPI's exception handlers — MED
See §3.1. This is the standard pattern, and it removes hundreds of lines of repeated `try`/`except`.

### 5.3 Use a library for process memory — LOW
`psutil` (or at least one internal module) instead of four `ctypes` implementations (§3.1).

### 5.4 Typed node configs — HIGH
- `NodeData.config` is `dict[str, Any]` (`_types.py:1012`).
- `validate_node_config` (`_config_validation.py:377`) is strict only for Data Input, Data Output and Banding.
- Every other type is checked by scattered hand-written validators: in the save service, `_optimiser_service._validate_config` (`:4628`), `_training_lifecycle._validate_config` (`:1139`), the recovery validators and the runtime builders.
- The TypedDicts only drive a key allowlist that silently drops unknown keys (§1.5).
- Per-node Pydantic models, discriminated by `nodeType`, would give one validation boundary and one source for the generated frontend types (§5.1). PCFG-R03 is a narrower step in this direction.

### 5.5 Slim the deployed runtime — MED
The core dependencies include `anthropic`, `openai`, `optuna`, `mlflow`, `scipy`, `pandas`, `catboost`, `rustystats`, `price-contour`, `libcst` and `watchfiles`. The scoring container installs the haute wheel, so every pricing API image carries the assistant SDKs, optuna and the editor's server dependencies. Split a scoring or runtime extra from the editor, assistant and training extras.

---

## 6. Specs, docs, policy and governance

- **Canonical-only policy versus the code.** The README says "the implementation has no branches or diagnostics that recognise historical Haute input". The code still has:
  - retired keys in `_node_config_recovery.reconcile_config` (`baseInput`, `joinInput`, `scored_input`, `factors_input`)
  - `_edge_join._LEGACY_ROLE_DECORATOR_ARGS` (`:32`)
  - `_config_validation.py:116`
  - the duplicated legacy modelling checks (§3.1)
  - `projection.py:4128` ("synthesising identity for legacy callers")
  - the assistant's legacy catalogue and examples
  - the Explore unknown-key round-trip
  - the silent key drop (§1.5)

  Decide the rule (for example, "targeted rejection messages are allowed; no migration and no silent drop") and make both sides agree. The same applies to "fail loud": pipeline-config's parse-time contract check falls back to an opaque contract on `ConfigError`, `OSError`, `ImportError`, `RuntimeError` or `MlflowException`, which its own spec calls "broader than infrastructure-only failure".
- **Spec drift found:**
  - Two specs still describe the dead dataframe execution cache (§2.1).
  - `expression-parsing/high-level.md:147-149` says codegen uses `tokenize` rewrites and not LibCST, but `_python_syntax.py` is LibCST.
  - The deploy spec says the source-cache lease registry is process-local, but `_source_cache.py` has cross-process lease markers.
  - The codegen spec's "never splice a manually located delimiter" versus `codegen.py:300-321`.
  - `server-api`'s `pipeline_dir` cwd fallback versus pipeline-config's "not being inside a project raises".
- **Governance effort points inward.** About 5K test lines, plus a 1.4K-line ledger, keep the *internal* spec corpus consistent (`test_docs_accuracy.py` 2,679, `test_workflow_coverage.py` 1,470, `workflow_coverage.toml` 1,360, `test_test_debt.py` 895, `spec_corpus_inventory.py`). Meanwhile the *user-facing* MkDocs node reference still documents removed node types (§1.3). Point some of that at `docs/`, for example by generating the node reference from the node config models once §5.4 exists.

---

## 7. Tests

- **Size and shape.** Backend tests total 421K lines (2.3× the source); `test_optimiser_routes.py` alone is 16,581 lines. According to the saved project notes, a full serial run takes about 40 minutes.
- **About 20K lines sit in files named after coverage campaigns or fix waves** rather than behaviour: `test_expression_parser_coverage` (3,166), `test_train_service_coverage` (2,556), `test_algorithms_coverage` (2,349), `test_json_cache_coverage_uplift`, `test_coverage_gaps`, `test_dry_fixes`, `test_*_w3_fixes`, `test_trace_w4_fixes` and `*_coverage` ×8. The frontend has `apiInputBundle3b`/`3c` tests. This is the typical result of a 100%-changed-line coverage gate. It makes tests hard to find by feature and rewards line-shaped assertions.
- **Deletions in §2 and §4 would remove large test blocks** with no loss of product coverage: about 3K lines for the dead cache, about 3.8K for chunking if §2.2's measurement allows it, and up to about 30K for the static-analysis stack if §4.1 is adopted.
- **Suggestion.** Reorganise the tests by component and behaviour. Keep the mutation and critical-file ratchets for the genuinely safety-critical code (rating, deploy scoring, feature contracts), and relax the changed-line gate to risk-based coverage elsewhere.

---

## 8. Where each finding is tracked

Every actionable finding is a package in its owning component roadmap. This
report owns no work; the packages carry the plan, acceptance and evidence.
Four items were already tracked when the review ran, and the new packages
build on them: `CACHE-S08` (one store for API-input tables), `ROAD-WORKER-04`
(isolating the optimiser, deferred), `OPT-P11` to `OPT-P14` (splitting the
optimiser service) and `PCFG-R03` (save-time config validation).

| Finding | Package |
|---|---|
| §1.1 Global Polars-config lock | `EXEC-R01` ([execution engine](execution-engine.md)) |
| §1.2 Deploy omits `utility/` | `DEP-R01` ([deploy](deploy.md)) |
| §1.3 User docs describe removed nodes | `BUILD-R01` (build and distribution, delivered; its roadmap file is retired) |
| §1.4 Project-root resolvers | `PCFG-R04` ([pipeline config](pipeline-config.md)) |
| §1.5 Silent config-key drop | `PCFG-R05` ([pipeline config](pipeline-config.md)) |
| §1.6 Frontier point derived twice | `OPT-P18` ([optimiser](optimiser.md)) |
| §1.7 Swallowed exceptions | `MLF-R01` ([MLflow model registry](mlflow-model-registry.md)), `MOD-T09` ([modelling](modelling.md)), `OPT-P17` ([optimiser](optimiser.md)) |
| §1.8 Unbounded poll loop | `FSH-R01` ([frontend shared](frontend-shared.md)) |
| §1.8 Three non-finite float encodings | `JSON-R01` ([JSON shredding](json-shredding.md)) |
| §1.8 Permissive dtype names; dtype mapping ×7 | `IO-R01` ([IO layer](io-layer.md)) |
| §1.8 Fixed-name atomic write; lock wrappers | `IO-R02` ([IO layer](io-layer.md)) |
| §1.8 Path containment ×5 | `SBX-R01` ([sandbox security](sandbox-security.md)) |
| §1.8 Client picks the build profile from error text | `CACHE-S27` ([caching](caching.md)) |
| §2.1 Dead dataframe execution cache | `CACHE-S23` ([caching](caching.md)) |
| §2.2 Chunked runner and streaming auto-range | `OPT-P15` ([optimiser](optimiser.md)), then `EXEC-R03` ([execution engine](execution-engine.md)) if the measurement removes the runner's consumer |
| §2.3 Unreferenced and test-only code | `ENGQ-R01` ([engineering quality](engineering-quality.md)); the preview-reader protocol is `TRACE-R02` ([tracing](tracing.md)) |
| §2.4 Per-request chunk-size knob | `EXEC-R01` ([execution engine](execution-engine.md)), with §1.1 |
| §2.5 Assistant legacy catalogue and harnesses | `ASSIST-R01` ([assistant](assistant.md)) |
| §2.6 Tracked artifacts and the `rating/` reference | `ENGQ-R02` ([engineering quality](engineering-quality.md)); the external assembler document is `JSON-R02` ([JSON shredding](json-shredding.md)) |
| §3.1 Error translation | `API-R01` ([server API](server-api.md)) |
| §3.1 HTTP types in services | `API-R02` ([server API](server-api.md)) |
| §3.1 Three worker mechanisms | `ROAD-WORKER-05` ([background jobs](background-jobs-api.md)) |
| §3.1 Process-memory probes ×4 | `EXEC-R06` ([execution engine](execution-engine.md)) |
| §3.1 Two stores, three freshness proofs, hand-rolled LRUs | `CACHE-S08` and `CACHE-S24` ([caching](caching.md)) |
| §3.1 Graph traversal ×5 | `EXEC-R08` ([execution engine](execution-engine.md)) |
| §3.1 Legacy modelling check ×2 | `PCFG-R06` ([pipeline config](pipeline-config.md)) |
| §3.1 Git outside the chokepoint | `DEP-R04` ([deploy](deploy.md)) |
| §3.2 Polling | `FSH-R01` ([frontend shared](frontend-shared.md)) |
| §3.2 Error, format, debounce, modal and table helpers | `FSH-R02` ([frontend shared](frontend-shared.md)) |
| §3.2 Editor clones and the read-only dispatcher | `FNE-R01` ([frontend node editors](frontend-node-editors.md)) |
| §3.2 Modelling tab and chart clones | `FMO-R01` ([frontend modelling and optimiser UI](frontend-modelling-optimiser-ui.md)) |
| §3.2 Results store | `FSH-R03` ([frontend shared](frontend-shared.md)) |
| §3.3 Invariants validated three times | `MOD-T10` ([modelling](modelling.md)) |
| §3.3 Editor state inside node config | `PCFG-R08` ([pipeline config](pipeline-config.md)) |
| §4.1 Static memory analysis versus hard caps | `EXEC-R04` ([execution engine](execution-engine.md)) |
| §4.2 One execution walker | `EXEC-R05` ([execution engine](execution-engine.md)) |
| §4.3 Optimiser pipeline in a capped worker | `OPT-P16` ([optimiser](optimiser.md)) |
| §4.4 Hand-written Polars interpreter | `EXPR-R01` ([expression parsing](expression-parsing.md)) |
| §4.5 Trace row identity | `TRACE-R01` ([tracing](tracing.md)) |
| §4.6 Recovery and repair scope | `API-R04` ([server API](server-api.md)) |
| §4.7 Four reuse mechanisms | `SUB-R01` ([submodels](submodels.md)) |
| §4.8 Implicit-join output assembly | `JSON-R02` ([JSON shredding](json-shredding.md)) |
| §4.9 Node-code guard | `SBX-R02` ([sandbox security](sandbox-security.md)) |
| §4.10 Codegen decorator splice | `CODEGEN-R01` (codegen, delivered; its roadmap file is retired) |
| §4.10 Node registry | `PCFG-R09` ([pipeline config](pipeline-config.md)) |
| §4.10 `ExecutionContext` size | `EXEC-R07` ([execution engine](execution-engine.md)) |
| §4.10 Cache identity framework | `CACHE-S25` ([caching](caching.md)) |
| §4.10 Scaffolding for unimplemented targets | `DEP-R03` ([deploy](deploy.md)) |
| §4.10 No automatic retention | `CACHE-S26` ([caching](caching.md)) |
| §4.10 Process-global library state | `EXEC-R01` ([execution engine](execution-engine.md)), `MLF-R02` ([MLflow model registry](mlflow-model-registry.md)) |
| §5.1 Generated browser contract | `API-R03` ([server API](server-api.md)) |
| §5.2 Exception handlers | `API-R01` ([server API](server-api.md)) |
| §5.3 Process-memory library | `EXEC-R06` ([execution engine](execution-engine.md)) |
| §5.4 Typed node configs | `PCFG-R07` ([pipeline config](pipeline-config.md)) |
| §5.5 Slim scoring runtime | `DEP-R02` ([deploy](deploy.md)) |
| §6 Canonical-only policy versus the code | `PCFG-R06` ([pipeline config](pipeline-config.md)) |
| §6 Specification drift | `ENGQ-R03` ([engineering quality](engineering-quality.md)), plus `CACHE-S23`, `CODEGEN-R01` and `PCFG-R04` for their own statements |
| §6 Governance pointed inward | `ENGQ-R04` ([engineering quality](engineering-quality.md)) |
| §7 Test organisation | `ENGQ-R05` ([engineering quality](engineering-quality.md)) |

Three §4.10 items are observations, not recommendations, and have no
package: the git branch-pair model, hosted project storage, and the size of
the assistant. They are defensible product choices, noted as large surfaces
to keep in view.

---

## Suggested order

1. **Quick, contained fixes:** §1.1, §1.2 (reject at validation first, bundle next), §1.3, §1.5 (reject instead of drop), §1.6, §1.7 and §1.8.
2. **Deletions:** §2.1, §2.3 and §2.4, and §2.2 once its memory measurement allows it. These carry low risk, remove about 4K source lines and about 8K test lines, and shrink the surface for everything after them.
3. **Consolidations with clear targets:** §5.1 (contract generation), §5.2 and §3.1 (errors), §1.4 (project context), §3.1 (worker primitive, RSS, locks), the §3.2 frontend helpers, and §5.4 (typed configs).
4. **Decisions, each with a short spec change first:** §4.1, §4.2, §4.3, §4.4, then §4.5–§4.9 as product priorities allow.

Per AGENTS.md, each behavioural change starts with its owning spec. The deletions in §2 mostly need spec *removals*: the caching and execution-engine text on the dataframe cache, and the chunked map-reduce sections.

---

## Appendix: metrics used

- **Radon, highest complexity:**
  - `_execute_eager_core` CC 191 (977 lines)
  - `_execute_lazy` CC 122 (1,062)
  - `_column_lineage._parse_call_sequence` CC 110
  - `carried_column_proof` CC 98
  - `_ExprEvaluator._call` CC 95
  - `generate_evaluation_plan` CC 87
  - `_validator_issues` CC 87
  - `enrich_steps` CC 78 (496 lines)
  - `_build_recovery_graph` CC 77
  - `_reset_node` CC 76
- **Largest modules:**
  - `routes/_optimiser_service.py` 5,681
  - `projection.py` 5,045
  - `types/guards.ts` 4,073
  - `schemas.py` 3,973
  - `_execute_lazy.py` 3,709
  - `_column_lineage.py` 3,576
  - `modelling/_training_job.py` 2,784
  - `executor.py` 2,593
- **Churn (commits since 2026-06-23):**
  - `schemas.py` 74
  - `api/types.ts` 69
  - `guards.ts` 61
  - `client.ts` 55
  - `_execute_lazy.py` 54
  - `_builders.py` 49
  - `App.tsx` 49
  - `routes/pipeline.py` 45
  - `NodePanel.tsx` 43
  - 136 of the 577 source commits in that window are fixes.
