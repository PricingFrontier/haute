# Codebase review — 31 October 2026

A whole-codebase functional and full-inventory source review at `edde9e169` (`main`),
recorded on `argon-review`. The working tree had no tracked changes when review began.
This report supports the component roadmaps and owns no work. New work is assigned to
23 new packages across 19 component roadmaps, plus extensions to `DEP-R12B`, `DEP-R13`
(both since delivered, with the deploy roadmap retired) and `MOD-R05`. It does not implement fixes or certify the repository as bug-free.

## Method and limits

The review proceeded in two passes across the backend (`src/haute/`), the frontend
(`frontend/src/`), and the specifications (`specs/`):

- **Pass 1 (subsystem-wide functional and contract pass):** End-to-end inspection of
  the primary entry points, state transitions, and cross-module contracts across all
  five functional areas below, paired with executed Python behavioural probes.
- **Pass 2 (100% file-by-file inventory ledger pass):** Every authored production
  `.py`, `.ts` and `.tsx` file in `src/haute/` (`353` files) and `frontend/src/`
  (`608` files, excluding `frontend/src/generated/` and `__tests__`/`*.test.*`) was
  catalogued in a scratch inventory ledger (`961` files, `376,420` lines across 16
  batches `B01`–`B08` and `F01`–`F08`) and read systematically until `0` unread
  files remained (`961/961` reviewed).

The functional areas covered:

1. **Parser, codegen, config IO/validation, recovery, and scoped repair** (`src/haute/parser.py`,
   `src/haute/_parser_*.py`, `src/haute/codegen.py`, `src/haute/_config_*.py`,
   `src/haute/_pipeline_recovery.py`, `src/haute/_pipeline_repair_*.py`,
   `src/haute/_recovery_sources.py`, `src/haute/routes/pipeline.py`,
   `src/haute/routes/submodel.py`).
2. **Execution engine, builders, projection, caching, chunking, tracing, and worker isolation**
   (`src/haute/executor.py`, `src/haute/execution.py`, `src/haute/_execute_lazy.py`,
   `src/haute/_graph_walker.py`, `src/haute/_builders.py`, `src/haute/projection.py`,
   `src/haute/_column_lineage.py`, `src/haute/_lru_cache.py`, `src/haute/_cache.py`,
   `src/haute/_node_snapshots.py`, `src/haute/_source_cache.py`, `src/haute/_seed_plans.py`,
   `src/haute/trace.py`, `src/haute/_trace_*.py`, `src/haute/_worker_*.py`,
   `src/haute/_interactive_workers.py`).
3. **Modelling, model scoring, optimiser, Explore/EDA, and data/file routes**
   (`src/haute/modelling/`, `src/haute/_model_*.py`, `src/haute/_mlflow_*.py`,
   `src/haute/_node_apply.py`, `src/haute/_optimiser_apply_explainability.py`,
   `src/haute/routes/modelling.py`, `src/haute/routes/_training_*.py`,
   `src/haute/routes/optimiser.py`, `src/haute/routes/_optimiser_*.py`,
   `src/haute/routes/explore.py`, `src/haute/routes/_pivot_service.py`,
   `src/haute/routes/files.py`, `src/haute/routes/input_cache.py`,
   `src/haute/routes/json_cache.py`, `src/haute/routes/output_assemble.py`).
4. **Deploy, CLI, Git integration, Assistant, Workbench, and Quote Store**
   (`src/haute/deploy/`, `src/haute/cli/`, `src/haute/_git_*.py`,
   `src/haute/_project_storage.py`, `src/haute/assistant/`,
   `src/haute/_workbench_*.py`, `src/haute/_quote_store/`,
   `src/haute/routes/git.py`, `src/haute/routes/workbench.py`).
5. **Frontend graph canvas, stores, hooks, node editors, modelling/optimiser UI, Explore UI, and Workbench/App UI**
   (`frontend/src/App.tsx`, `frontend/src/stores/`, `frontend/src/hooks/`,
   `frontend/src/panels/`, `frontend/src/workbench/`, `frontend/src/app/`,
   `frontend/src/components/`, `frontend/src/nodes/`, `frontend/src/utils/`).

Candidates were compared against all existing component roadmaps and the
[23 September](codebase-review-2026-09-23.md), [25 October](codebase-review-2026-10-25.md)
and [28 October](codebase-review-2026-10-28.md) review reports to avoid duplicating
already-tracked issues. The main session verified every accepted finding directly
against the current source lines on `HEAD` and ran targeted Python behavioural probes
in the installed Windows environment (Python 3.11.13, Polars 2.0.0) for key backend
invariants. Probe scripts were kept outside the repository in the scratch directory.

## New findings and ownership

Priorities follow the roadmap convention: `P1` threatens saved work, calculated
results, or security; `P2` materially affects correctness, UX, or assurance; `P3`
covers opportunistic or low-exposure items.

| Priority | Trigger and observed consequence | Owning package | Evidence |
|---|---|---|---|
| P2 | `useNodeDataCache` keeps `ownedJobId` in local component `useState` instead of `useNodeDataStore`, losing Stop/Cancel across tab and node switches; `loadPipelineSettings` overwrites `_confirmedPipelineSettings` before its sequence/pending guard; `UtilityPanel` uses static `key="imports"` with `defaultValue={preamble}`. | [FSH-R13](frontend-shared.md#fsh-r13--store-backed-node-data-job-ownership-guarded-pipeline-settings-load-and-utilitypanel-preamble-buffer-sync) | Source inspection of `frontend/src/hooks/useNodeDataCache.ts::useNodeDataCache`, `frontend/src/stores/useSettingsStore.ts::useSettingsStore` and `frontend/src/panels/UtilityPanel.tsx::UtilityPanel`. |
| P2 | `PivotTableGrid` indexes `pivot.columns[level]` and `pivot.rows[level]` by position instead of matching `result.column_fields[level]` / `result.row_fields[level]`; `ExplorePivotsConfig` and `ExploreChartsConfig` write closure-captured arrays after `confirmed(...)`; `ActiveNodePreviewBody` omits `errorAction` on `ExplorePreview`. | [EDA-R04](explore-eda.md#eda-r04--pivot-header-format-lookup-by-field-name-fresh-config-reads-after-confirmation-dialogs-and-explore-error-assistant-action) | Source inspection of `frontend/src/panels/explore/PivotTableGrid.tsx::PivotTableGrid`, `frontend/src/panels/editors/ExplorePivotsConfig.tsx::ExplorePivotsConfig`, `frontend/src/panels/editors/ExploreChartsConfig.tsx::ExploreChartsConfig` and `frontend/src/App.tsx`. |
| P2 | `output_assemble_dry_run` returns HTTP 500 instead of 422 in default `process` mode on `ContractColumnsMissingError` / `ContractMismatchError` raised during graph execution and omits `_ensure_source_file(graph)`; `get_schema` gates `read_parquet_metadata` (`row_count`) on `target.suffix` instead of `reader.format`; `NodeDataService._fail_job` exposes `str(exc.to_payload())` dict reprs; `project_files._plan_write` creates destination parent directories outside `project_write`; `parsePreviewNodeResponse` strips `notes`. | [API-R04](server-api.md#api-r04--process-mode-422-domain-error-mapping-in-output_assemble_dry_run-format-keyed-parquet-metadata-reads-in-get_schema-node-data-memory-error-formatting-project_files-destination-mkdir-locking-and-parsepreviewnoderesponse-notes) | Source inspection of `src/haute/routes/output_assemble.py::output_assemble_dry_run`, `src/haute/routes/files.py::_read_schema_blocking`, `src/haute/routes/_node_data_service.py::NodeDataService._fail_job`, `src/haute/routes/project_files.py::_plan_write` and `frontend/src/types/guards.ts::parsePreviewNodeResponse`. |
| P2 | Explicit `/api/input-cache/build` for `"bounded"` Data Inputs runs in-process on a server daemon thread with `execution_context=None`, bypassing memory admission and `run_supervised_data_input_build`; `input_cache` and `json_cache` resolve paths from `Path.cwd()`; `_infer_jsonl_in_parallel` re-walks the prefix range already scanned by `_learn_jsonl_prefix`. | [IO-R07](io-layer.md#io-r07--memory-admission-and-isolated-worker-execution-for-explicit-data-input-cache-builds-project-root-path-anchoring-and-non-overlapping-parallel-jsonl-schema-inference) | Source inspection of `src/haute/routes/input_cache.py::_run_build`, `src/haute/_input_preparation.py::_run_build`, `src/haute/routes/json_cache.py::_resolve_data_path` and `src/haute/_json_shred/_inference.py::_infer_jsonl_in_parallel`. |
| P2 | `_report_table` in `_tboost.py` does not guard `np.exp(scores)` overflow (`inf`), causing `_assert_json_finite` in `_training_worker.py` to abort the entire training job instead of isolating to a `"diagnostic": "tboost_tables"` record in `diagnostics_errors`. | [TBOOST-11](t-boost.md#tboost-11--guard-_report_table-relativity-overflow) | Source inspection of `src/haute/modelling/_tboost.py::_report_table`, `src/haute/modelling/_training_job.py::TrainingJob._compute_metrics` and `src/haute/routes/_training_worker.py::_assert_json_finite`. |
| P2 | `_impact_records` resolves `impact_dataset` without `contained_path` and drops non-string scalar fields on `test_quotes_dir` fallback; `lint` and `run` catch only `FileNotFoundError` (not `ConfigError`) around `resolve_pipeline_file`. | [CLI-R07](cli.md#cli-r07--impact-dataset-confinement-and-non-string-scalar-fields-and-lintrun-pipeline-resolution-errors) | Source inspection of `src/haute/cli/_impact.py::_impact_records`, `src/haute/cli/_lint.py::lint` and `src/haute/cli/_run.py::run`. |
| P2 | `useQuoteListStore` does not reset or fence `loadingMore` across list switches; `priceSample` searches only top-level `graph.nodes` for `WORKBENCH_OUTPUT`; `specs/workbench/high-level.md` retains a stale `The blank form is a form` bullet. | [WB-41](workbench.md#wb-41--fence-loadingmore-in-usequoteliststore-resolve-submodel-workbench_output-runtime-targets-in-pricesample-and-remove-stale-blank-form-rationale) | Source inspection of `frontend/src/app/useQuoteListStore.ts::useQuoteListStore`, `frontend/src/workbench/priceSample.ts::priceSample` and `specs/workbench/high-level.md`. |
| P2 | `_PreparedGraph.build`, `_bound`, `data_check_visibility` and `_prepare_column_profile` in `src/haute/assistant/` hardcode `project_root=Path.cwd()`; `_write_cache` and `SessionStore._persist` write to fixed `.json.tmp` paths without `atomic_write_text`; `_graph_fact` does not wrap `parse_pipeline_to_graph` failures in `ProjectKnowledgeError`. | [ASSIST-62](assistant.md#assist-62--project-root-workbench-form-resolution-atomic-cachesession-writes-and-resilient-_graph_fact-parsing) | Source inspection of `src/haute/assistant/_application.py::_PreparedGraph.build`, `src/haute/assistant/_data_check.py::_bound`, `src/haute/assistant/_project_knowledge.py::_write_cache`, `src/haute/assistant/_project_knowledge.py::_graph_fact` and `src/haute/assistant/_session.py::SessionStore._persist`. |

## Validation — 2 November 2026

Delivering `MOD-R10` did not reproduce its evaluation-preview finding: string date values
that `datetime.fromisoformat` cannot parse, or that mix naive and timezone-aware values, never
reach `_evaluation_preview_payload`'s sort, because `generate_evaluation_plan` parses the same
values the same way and refuses them first (the preview answers 422 naming the value), and
values with different offsets sort by instant. A test pins both; the preview was not changed.
