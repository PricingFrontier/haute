# Haute Codebase Review

Date: 2026-04-08

Reviewed against current checked-out tree:
- branch: `main`
- head observed during revalidation: `0b8e170`

Scope:
- backend/library code under `src/haute`
- React frontend under `frontend/src`
- tests, docs, and project metadata

Out of scope:
- [`main.py`](/home/ralph/suite/haute/main.py) as an engineering target, per request
- remote changes not present in the current local checkout

## Review Method

- Read the product and architecture material in [`README.md`](/home/ralph/suite/haute/README.md) and [`docs/ARCHITECTURE.md`](/home/ralph/suite/haute/docs/ARCHITECTURE.md).
- Re-reviewed the original high-risk areas against the current tree: sandboxing, parser/config loading, save/codegen invariants, submodel round-tripping, frontend quality, and repo baselines.
- Validated findings with targeted repros plus current test/build/lint runs.

Commands run during the current-state refresh:
- `./haute/.venv/bin/python -m pytest haute/tests/test_sandbox.py -q`
- `npm test`
- `npm run lint`
- `npm run build`
- `./haute/.venv/bin/python -m ruff check haute/src/haute haute/tests --statistics`

## Executive Summary

Active findings on the current checkout:
- High: 1
- Medium: 3
- Low: 3

The biggest change since the earlier pass is that two previously critical/high security items are no longer active findings in this checkout:
- `safe_joblib_load()` has been hardened
- project-root enforcement for config sidecars has been added

The main remaining concerns are now:
- duplicate sanitized node names can still corrupt generated code and graph identity
- generated file-backed node code is still sensitive to process `cwd`
- submodel create/dissolve still drops pipeline descriptions
- the frontend still has a real invalid-DOM warning in tests, a very large production bundle, and stale local docs
- the Python lint baseline is still far from clean

## Findings

### 1. High: duplicate sanitized node labels are still not rejected before codegen/save

Why it matters:
- Haute uses human labels to derive Python function names and config file names.
- Two distinct nodes that sanitize to the same identifier can overwrite config names, emit ambiguous code, and collapse identity on reparse.

Evidence:
- [`_sanitize_func_name()`](/home/ralph/suite/haute/src/haute/_types.py#L579) defines the normalized identifier used throughout the stack.
- [`PipelineGraph.node_map`](/home/ralph/suite/haute/src/haute/_types.py#L554) silently collapses duplicate IDs.
- [`_node_to_code()`](/home/ralph/suite/haute/src/haute/codegen.py#L345) derives config paths from the sanitized label.
- [`_build_id_to_func()`](/home/ralph/suite/haute/src/haute/codegen.py#L893) maps node IDs to sanitized function names with no collision handling.
- [`_validate_singletons()`](/home/ralph/suite/haute/src/haute/routes/_save_pipeline.py#L80) still only enforces singleton node types, not sanitized-name uniqueness.

Validation:
- I reproduced this with two nodes both labeled `same`.
- `graph_to_code()` emitted two `def same(...)` definitions.
- Reparsing left `node_map` with only one key: `same`.

Recommendation:
- Reject saves when two nodes sanitize to the same function/config name.
- Validate both direct duplicates and sanitized collisions like `a-b` vs `a b`.

### 2. Medium: generated file-backed nodes still resolve relative paths against process `cwd`

Why it matters:
- Generated pipelines should be portable when imported or executed from outside the project root.
- Haute already uses the better `Path(__file__).parent` pattern for model-scoring config paths, so the inconsistency is avoidable.

Evidence:
- [`_data_source_parts()`](/home/ralph/suite/haute/src/haute/codegen.py#L185) emits raw `pl.scan_*()` / `pl.read_json()` calls using string paths.
- [`_api_input_template()`](/home/ralph/suite/haute/src/haute/codegen.py#L146) does the same for API-input file paths.
- [`score_from_config()` path handling](/home/ralph/suite/haute/src/haute/_model_scorer.py#L413) documents the safer pattern: codegen passes `Path(__file__).parent` so runtime does not depend on `cwd`.

Validation:
- I generated a pipeline in `project/main.py` with a data source path `data/x.parquet`.
- Importing that pipeline from the parent directory still fails with `FileNotFoundError`.

Recommendation:
- Resolve file-backed node paths relative to the pipeline module file or an explicit project root.
- Add an integration test that imports a generated pipeline from outside the project directory.

### 3. Medium: submodel create/dissolve flows still drop pipeline descriptions

Why it matters:
- Pipeline metadata should survive editor operations.
- Losing descriptions weakens the code-as-source-of-truth promise and creates needless churn in generated files.

Evidence:
- [`PipelineGraph.pipeline_description`](/home/ralph/suite/haute/src/haute/_types.py#L544) exists in the core graph model.
- [`CreateSubmodelRequest`](/home/ralph/suite/haute/src/haute/schemas.py#L396) and [`DissolveSubmodelRequest`](/home/ralph/suite/haute/src/haute/schemas.py#L412) do not carry a description field.
- [`create_submodel()`](/home/ralph/suite/haute/src/haute/routes/submodel.py#L57) and [`dissolve_submodel()`](/home/ralph/suite/haute/src/haute/routes/submodel.py#L148) generate code without passing a description through.
- [`graph_to_code_multi()`](/home/ralph/suite/haute/src/haute/codegen.py#L1165) still hardcodes submodel description to `""`.

Validation:
- I reproduced this with `graph_to_code_multi()` using a graph whose `pipeline_description` was set.
- The generated main pipeline line was:
  - `pipeline = haute.Pipeline("p", description='')`

Recommendation:
- Carry description through the submodel request/response path and codegen calls.
- Treat description as part of the same round-trip contract as `preamble` and preserved blocks.

### 4. Medium: the frontend still contains an invalid nested `<button>` pattern that triggers a hydration warning in tests

Why it matters:
- This is invalid HTML, hurts accessibility semantics, and React explicitly warns it can cause hydration errors.
- Even if Haute is mostly client-rendered today, invalid interactive nesting is not a standard worth carrying forward.

Evidence:
- [`RatingStepEditor.tsx`](/home/ralph/suite/haute/frontend/src/panels/editors/RatingStepEditor.tsx#L154) renders a tab as a `<button>`.
- [`RatingStepEditor.tsx`](/home/ralph/suite/haute/frontend/src/panels/editors/RatingStepEditor.tsx#L169) renders a second `<button aria-label="Remove table">` inside that parent button.

Validation:
- The full frontend test run passes, but it emits React’s warning:
  - “In HTML, `<button>` cannot be a descendant of `<button>`. This will cause a hydration error.”

Recommendation:
- Change the outer tab control to a non-button wrapper with an internal button, or move the remove affordance outside the clickable tab button.
- Make frontend tests warning-free for invalid DOM structure, not just green.

### 5. Low: the frontend production build is still shipping as one very large JS entry chunk

Why it matters:
- The current bundle size is high enough to be a real maintainability and load-performance concern.
- It signals that the app shell and heavy panels are still being pulled into the initial path too aggressively.

Evidence:
- `npm run build` produced one JS asset at `2,776.43 kB` minified and `833.83 kB` gzip.
- [`vite.config.ts`](/home/ralph/suite/haute/frontend/vite.config.ts#L11) defines the build but still has no chunking strategy.

Recommendation:
- Split heavier panels and secondary workflows with `lazy()` / dynamic imports.
- Add bundle-size thresholds to CI once chunking is in place.

### 6. Low: the Python repo is still far from lint-clean under the committed Ruff baseline

Why it matters:
- A noisy lint baseline weakens CI as a quality signal.
- This is especially important for a framework codebase where maintainability standards should be machine-enforced.

Evidence:
- [`pyproject.toml`](/home/ralph/suite/haute/pyproject.toml#L103) commits Ruff rules for `E`, `F`, `I`, `N`, `W`, and `UP`.
- Current Ruff statistics report `472` findings, including:
  - `151` `E501`
  - `132` `F401`
  - `116` `I001`

Recommendation:
- Either clean the baseline or deliberately narrow the enforced rule set.
- I would prioritize `F*` and `I*` classes first because they are the highest-signal issues.

### 7. Low: `frontend/README.md` is still the stock Vite template

Why it matters:
- The subproject doc that frontend contributors are most likely to open still describes a generic scaffold instead of Haute’s actual UI architecture and commands.

Evidence:
- [`frontend/README.md`](/home/ralph/suite/haute/frontend/README.md#L1) is still the default “React + TypeScript + Vite” template.

Recommendation:
- Replace it with a short frontend-specific README covering:
  - dev/test/lint/build commands
  - stores/hooks architecture
  - API integration with the FastAPI server
  - bundle and test expectations

## Open Questions / Policy Decisions

### Out-of-root `config=` references are now blocked, but the UX contract should be made explicit

What changed:
- [`load_node_config()`](/home/ralph/suite/haute/src/haute/_config_io.py#L115) now rejects paths outside the project root.
- [`_resolve_node_config()`](/home/ralph/suite/haute/src/haute/_parser_helpers.py#L974) now converts that into `_load_error` metadata instead of loading the external file.

Current behavior:
- This is no longer the earlier security bug.
- But it is still a product/design choice: should invalid external config paths preserve the node with `_load_error`, or should parse/save fail hard and visibly?

My view:
- The current security posture is much better.
- The remaining task is to make the UX/contract explicit and regression-tested.

## Recently Addressed Since The Earlier Pass

These are no longer active findings in the current checkout.

### `safe_joblib_load()` hardening

- [`safe_joblib_load()`](/home/ralph/suite/haute/src/haute/_sandbox.py#L414) now uses the same two-part allowlist semantics as `_RestrictedUnpickler` and wraps the monkey-patch in a lock.
- `haute/tests/test_sandbox.py` now includes explicit coverage for `builtins.eval` / `builtins.exec` style cases.

### Config sidecar root validation

- [`load_node_config()`](/home/ralph/suite/haute/src/haute/_config_io.py#L115) now validates resolved config paths against the project root.
- Parser fallback behavior now preserves `_load_error` rather than loading external JSON.

## Validation Signals

### Backend

- `./haute/.venv/bin/python -m pytest haute/tests/test_sandbox.py -q`
  - Result: `216 passed, 1 xfailed`
  - Takeaway: sandbox coverage is materially stronger than in the earlier pass.

### Frontend

- `npm test`
  - Result: `132` test files passed, `2550` tests passed
  - Takeaway: functional coverage is strong, but the run is not warning-free and still emits noisy stderr from expected error paths plus the nested-button DOM warning above.

- `npm run lint`
  - Result: passed

- `npm run build`
  - Result: passed
  - Build artifact warning: one `2,776.43 kB` minified JS chunk (`833.83 kB` gzip)

### Repo Quality

- `./haute/.venv/bin/python -m ruff check haute/src/haute haute/tests --statistics`
  - Result: `472` findings

## Areas Reviewed Without Additional Validated Findings

- executor/lazy runtime architecture under [`src/haute`](/home/ralph/suite/haute/src/haute)
- most route-level path validation and save-path hardening
- deploy/config structure under [`src/haute/deploy`](/home/ralph/suite/haute/src/haute/deploy)
- frontend state/store shape across the main app shell

That is not a claim that those areas are perfect. It means I did not find additional issues there that I could validate strongly enough to include as active findings in this report.

## Recommended Priority Order

1. Reject duplicate sanitized node/function/config names before save/codegen.
2. Fix file-backed path resolution so generated pipelines are not `cwd`-sensitive.
3. Preserve pipeline descriptions through submodel create/dissolve flows.
4. Fix the nested-button DOM issue in [`RatingStepEditor.tsx`](/home/ralph/suite/haute/frontend/src/panels/editors/RatingStepEditor.tsx#L154).
5. Reduce frontend bundle size and replace the frontend Vite-template README.
6. Clean or intentionally narrow the Python Ruff baseline.
