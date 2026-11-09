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
| P2 | `useQuoteListStore` does not reset or fence `loadingMore` across list switches; `priceSample` searches only top-level `graph.nodes` for `WORKBENCH_OUTPUT`; `specs/workbench/high-level.md` retains a stale `The blank form is a form` bullet. | [WB-41](workbench.md#wb-41--fence-loadingmore-in-usequoteliststore-resolve-submodel-workbench_output-runtime-targets-in-pricesample-and-remove-stale-blank-form-rationale) | Source inspection of `frontend/src/app/useQuoteListStore.ts::useQuoteListStore`, `frontend/src/workbench/priceSample.ts::priceSample` and `specs/workbench/high-level.md`. |

## Validation — 2 November 2026

Delivering `MOD-R10` did not reproduce its evaluation-preview finding: string date values
that `datetime.fromisoformat` cannot parse, or that mix naive and timezone-aware values, never
reach `_evaluation_preview_payload`'s sort, because `generate_evaluation_plan` parses the same
values the same way and refuses them first (the preview answers 422 naming the value), and
values with different offsets sort by instant. A test pins both; the preview was not changed.
