# PR #23 Review Continuation

Scope: continuation of `PR23_REVIEW_RESULTS.md` and `PR23_REVIEW_PHASE1.md` on branch
`wave-2-cache-integrity`, including the current working tree.

Status:
- Phase 2 is complete. The two outstanding candidates, PRJ-1 and SNK-1, are confirmed.
- Phase 3 sweep was launched across backend core, server/deploy/security, frontend, and tests/config.
- Tests/config sweep found no new confirmed candidates.

## Phase 2 Confirmed

### 1. HIGH - Projection rename demand drops required source columns

Evidence:
- `src/haute/projection.py:1084` handles `rename` by translating only current downstream demand.
- `src/haute/projection.py:1027` maps only columns already present in the demand set.
- `src/haute/projection.py:1124` can let a later `with_columns(... alias(...))` reduce demand to expression refs.
- `_execute_lazy.py` applies the resulting parent edge projection before the child node executes.

Repro probe:
```text
code = "df = df.rename({'customer_id': 'cid'})\n"
       "df = df.with_columns((pl.col('premium') * 2).alias('p2'))"
_single_parent_polars_expression_demands(code, {'p2'}) == {'premium'}
```

Failure mode: the parent frame is projected to `premium`, stripping `customer_id`. The transform then executes
`rename({'customer_id': 'cid'})` and fails with a missing-column error, although the same graph succeeds without
projection.

Top-15 impact: should enter the top 15. It is stronger than the previous weakest git/archive item because it turns a
valid pipeline into a runtime failure.

### 2. HIGH - Sink output paths use input-style "existing files win" resolution

Evidence:
- `src/haute/executor.py:1385` resolves sink output paths through `resolve_runtime_file_path(... prefer="pipeline")`.
- `src/haute/_path_resolution.py:81` documents the shared resolver as project-root GUI path resolution with
  pipeline-relative fallback.
- `src/haute/_path_resolution.py:126` returns an existing candidate before a missing preferred candidate.
- `src/haute/routes/pipeline.py:750` passes `project_root` into sink execution for API-submitted graphs.

Repro probe:
```text
root/outputs/out.parquet exists
graph.source_file = "pipelines/pipeline_b.py"
resolve_sink_output_path(graph, "out", "parquet", project_root=root)
=> root/outputs/out.parquet
```

Failure mode: a nested pipeline writing `out` overwrites an existing project-root `outputs/out.parquet` instead of
creating `pipelines/outputs/out.parquet`.

Top-15 impact: should also be considered for top 15, competing with the previous git/archive finding.

## Phase 3 New Confirmed Findings

### 3. HIGH - `delete_branch` deletes the remote before local checkout can fail

Evidence:
- `src/haute/_git.py:933` creates the backup tag.
- `src/haute/_git.py:935` pushes the backup tag.
- `src/haute/_git.py:942` deletes the remote branch.
- `src/haute/_git.py:950` only then checks out the default branch.
- `src/haute/_git.py:953` deletes the local branch.

Failure mode: deleting the currently checked-out branch with dirty/conflicting work can delete the remote branch first,
then fail to checkout the default branch, leaving remote and local state split. This is the same ordering bug family as
the previously confirmed `archive_branch` finding, but in `delete_branch`.

### 4. HIGH - Recursive submodel config sidecars silently overwrite each other

Evidence:
- `src/haute/routes/_save_pipeline.py:222` validates unique sanitized names only across `graph.nodes`.
- `src/haute/_config_io.py:346` derives sidecar path from node type plus sanitized label.
- `src/haute/routes/_save_pipeline.py:592` collects parent configs.
- `src/haute/routes/_save_pipeline.py:594` merges nested configs with `dict.update(...)`.

Repro probe:
```text
submodel A has dataSource label "Shared" path x.csv
submodel B has dataSource label "Shared" path y.csv
_collect_node_configs_recursive(...)
=> {'config/data_source/Shared.json': ... y.csv ...}
```

Failure mode: two config-backed nodes in different embedded submodels with the same sanitized label/type write the same
sidecar path, and the later recursive merge silently replaces the earlier config.

### 5. MEDIUM/HIGH - WebSocket source filtering stays on the parent file after drilling into a submodel

Evidence:
- `frontend/src/App.tsx:264` passes `sourceFileRef` into WebSocket sync.
- `frontend/src/hooks/useWebSocketSync.ts:178` filters `graph_update` by `sourceFileRef.current`.
- `frontend/src/hooks/useWebSocketSync.ts:281` filters `parse_error` by `sourceFileRef.current`.
- `frontend/src/hooks/useSubmodelNavigation.ts:88` pushes the submodel view with `file: modules/<name>.py`.
- `frontend/src/hooks/useSubmodelNavigation.ts:165` replaces the canvas with submodel nodes, but the hook never updates
  `sourceFileRef.current`.

Failure mode: while drilled into a submodel, updates/errors for the submodel file can be ignored as foreign, and parent
pipeline frames can still apply to the submodel canvas.

### 6. MEDIUM - Healthy AST parser can extract wrong function bodies around form-feed line breaks

Evidence:
- `src/haute/_ast_helpers.py:266` uses `source.splitlines()`.
- `src/haute/_ast_helpers.py:272` indexes that list with AST `lineno`.
- `src/haute/parser.py:152` uses `_extract_function_bodies` on the healthy AST path.

Failure mode: characters such as form feed are treated as line boundaries by `splitlines()` but not by AST line
numbering. A decorated function body can be shifted, causing the parser to attach the wrong source text to a node.
This is the same line-numbering class as the previous fallback-parser candidate, but it also exists on the healthy AST
path.

### 7. MEDIUM - Right-join projection recovery can demand a left-origin output from the right parent

Evidence:
- `src/haute/projection.py:1455` treats outputs missing from `inputs_by_parent` as recoverable through join inference.
- `src/haute/projection.py:2019` chooses a preserved parent for remaining unsuffixed output columns.
- `src/haute/projection.py:2023` chooses the right parent for `how="right"`.
- `src/haute/projection.py:2027` demands all remaining columns from that preserved parent.

Repro probe:
```text
right join, contract inputs_by_parent omits left_value
downstream demands left_value
planner demands left_value from right and only quote_id from left
```

Failure mode: an incomplete fan-in contract should fail loudly or widen; instead projection can prune the left-origin
column from the left parent and demand it from the right parent.

### 8. MEDIUM - Deploy test quote parsing unwraps a legitimate single field named `input`

Evidence:
- `src/haute/deploy/_validators.py:55` treats a row whose only non-metadata key is object-valued `input` as golden
  format.
- `src/haute/deploy/_validators.py:95` returns the nested object as the scoring input.

Repro probe:
```text
_parse_test_quote_case({"input": {"raw": 1}}, row_index=0).input
=> {"raw": 1}
```

Failure mode: a flat quote for a model whose only API field is literally `input` is parsed as golden-format wrapper
syntax and scored as `{"raw": 1}` instead of `{"input": {"raw": 1}}`.

## Phase 3 Plausible / Lower Priority

### P1. MEDIUM - Preview fan-out cannot be aborted and refresh can stampede upstream previews

Evidence:
- `frontend/src/hooks/usePipelineAPI.ts:379` launches downstream preview propagation without a `signal`.
- `frontend/src/hooks/usePipelineAPI.ts:425` passes the abort signal only to the root preview.
- `frontend/src/hooks/usePipelineAPI.ts:587` launches stale upstream refresh previews through `Promise.all(...)` with no
  signal or concurrency cap.

Impact: stale UI results are suppressed, but expensive backend work continues after cancellation/supersession; refresh
can issue many parallel upstream previews.

### P2. MEDIUM - Deploy artifact path fingerprints full-hash every request

Evidence:
- `src/haute/execution.py:367` defines `dataframe_paths_input_fingerprint`.
- `src/haute/execution.py:377` calls raw `_runtime_path_fingerprint`, not the stat-gated memo.
- `src/haute/deploy/_scorer.py:777` includes artifact path fingerprints in scoring cache keys.

Impact: deployed scoring with remapped large artifacts can re-read and hash artifact bytes for every cache-key build.
This overlaps the existing CLN-1 stat-gated fingerprint cleanup but is a distinct call path.

### P3. LOW/MEDIUM - Targeted preview/trace cache invalidates on unrelated runtime files

Evidence:
- `src/haute/execution.py:553` signs every file-backed runtime input in the graph.
- `src/haute/executor.py:901` uses those keys for preview cache fingerprints.
- `src/haute/trace.py:356` reconstructs the same runtime input keys for trace.

Impact: a file on an unrelated graph branch invalidates previews/traces for the current target. This is unnecessary
work, not stale/wrong data.

### P4. LOW/MEDIUM - Rating grid copy can override native input copy after stale multi-cell selection

Evidence:
- `frontend/src/panels/editors/rating/TwoWayGrid.tsx:220` allows native input copy only when there is no multi-cell
  selection.
- `frontend/src/panels/editors/rating/TwoWayGrid.tsx:230` otherwise writes selected grid TSV.

Impact: if a multi-cell selection remains while an input has selected text, Ctrl+C can copy the grid range instead of
the input selection. Mouse focus appears to clear this in common paths, so this is lower confidence/lower severity.

## Rerank Note

The previous `PR23_REVIEW_RESULTS.md` top 15 is stale. PRJ-1 should enter the top 15. SNK-1 and the new
`delete_branch` ordering issue should be considered alongside the previous #15 `archive_branch` item. The recursive
submodel config overwrite is also strong enough to be considered high priority because it silently corrupts persisted
user configuration.

## Agent Coverage

- Phase 2 verifier agents independently confirmed PRJ-1 and SNK-1.
- Backend core sweep produced four candidates; three were verified as confirmed or overlapping, one kept as lower-priority
  efficiency debt.
- Server/deploy/security sweep produced four candidates; three were verified as confirmed, one kept as plausible.
- Frontend sweep produced three candidates; one confirmed high-impact, two kept as plausible/lower priority.
- Tests/config sweep reported no new confirmed candidates and reported:
  - `uv run pytest tests/test_frontend_bundle_budget_ci.py tests/test_backend_frontend_contracts.py tests/test_schema_snapshots.py -q`
    passed.
  - `npm run test:benchmark:pr` passed.
  - `npx playwright test --list --grep "@smoke"` listed chromium, firefox-smoke, and mobile-chrome-smoke.
