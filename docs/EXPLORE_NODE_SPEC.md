# Explore Node Spec

Status: shipped (v1 - post-code preview, analysis dataset caching, UI shell)
Owner: Explore node workstream
Last updated: 2026-05-18

## Current Scope

The Explore node is an analysis-only sink for inspecting the full dataframe at
any point in a pipeline. In v1 it focuses on:

- optional Polars preparation code for analysis-only filtering/derived columns;
- previewing the top N rows after that Explore code, using the toolbar preview
  row limit;
- materialising the full post-code Explore dataframe into the existing
  dataframe execution cache;
- shared lower-panel chrome for node name, node icon, resizing, collapsing, and
  top expansion;
- right-panel pane scaffolding for the next EDA surfaces.

What ships:

- `explore` node type in the palette, rendered after `Rating Step` with its own
  purple explore colour and search icon.
- `@pipeline.explore` decorator that round-trips through parser and codegen.
- Optional `config["code"]` support. The code editor follows the existing
  Polars editor convention: user code assigns to `df`, generated Python aliases
  the single upstream input to `df`, and generated code returns `df`.
- Graph-shape contract: Explore nodes must have exactly one incoming edge and
  zero outgoing edges for execution. Previewing an unconnected draft Explore
  node returns a node-level preview error instead of breaking unrelated preview
  requests.
- `/api/explore/run`, `/api/explore/status/{job_id}`, and
  `/api/explore/cancel/{job_id}` endpoints backed by a background-job worker.
- Typed `ExploreCacheReport` response containing node id, upstream node id,
  source, dataframe cache key, row count, column count, generated timestamp,
  and execution metrics.
- Lower-panel `ExplorePreview` with tabs for `Preview`, `Overview`,
  `Relationships`, and `Charts`. `Preview` embeds the shared dataframe preview
  table after the Explore Polars code. The remaining tabs are intentionally
  empty scaffolding for future analysis.
- Right-panel Explore panes for `Polars Code`, `Overview`, `Relationships`,
  `Charts`, and `Export`, with per-node pane memory. Only `Polars Code` has an
  active editor in v1.

What does not ship in v1:

- populated overview, relationship, chart, or export sections;
- data-quality metrics, warnings, target-aware diagnostics, or segmentation;
- chart builder state or persisted chart definitions;
- export artifact generation.

These belong in follow-up versions, building on the cached post-code dataframe.

## Approach

The Explore node is a terminal analysis branch in the visual graph:

- `maxInputs: 1`
- no output handle
- no production side effects beyond its analysis cache

Users can branch an Explore node off any intermediate pipeline step, add
analysis-only Polars preparation, inspect the resulting preview rows, then
materialise the full resulting dataframe for heavier EDA work.

## Codegen, Parser, and Deploy Semantics

Haute's Python file remains the source of truth, so Explore nodes round-trip
through generated pipeline code rather than living only in frontend state.

Passthrough Explore:

```python
@pipeline.explore()
def inspect_claims(claims: pl.LazyFrame) -> pl.LazyFrame:
    """Optional user-authored description."""
    return claims
```

Explore with analysis code:

```python
@pipeline.explore()
def inspect_claims(claims: pl.LazyFrame) -> pl.LazyFrame:
    """Optional user-authored description."""
    df = claims
    df = df.filter(pl.col("premium") > 0)
    return df
```

Key semantics:

- parser and codegen preserve the node, description, optional Polars code, and
  graph edges;
- generated boilerplate is stripped back out of `config["code"]` on parse so
  save/reload cycles do not accumulate aliases or trailing returns;
- Explore has no output handle in the UI, so it cannot be connected into a
  production scoring path through normal UI operations;
- deploy bundles prune nodes that are not ancestors of an output naturally, and
  the graph-shape contract guarantees Explore cannot be such an ancestor;
- if a malformed source file makes an Explore node an ancestor of a production
  output, parse-time validation raises `ParseError` before execution starts.

## Execution and Caching

Preview and full-data caching both execute to the Explore node itself. This is
important because the displayed and cached dataset must include any Explore
Polars code, not just the upstream parent output.

The full-data Explore execution path:

1. Validate that the selected node exists, has node type `explore`, and has
   exactly one upstream parent.
2. Compile the pipeline preamble using the same executor helper used by
   training and optimiser.
3. Build a `DataFrameExecutionCacheRequest` for the Explore node in the
   `explore_dataset` namespace.
4. Execute the graph lazily to the Explore node with the current source and the
   `ExecutionProfile.EXPLORE_ANALYSIS` profile.
5. Materialise the full Explore output through `DataFrameExecutionCache`; cache
   hits return a parquet scan without rerunning unchanged upstream work.
6. Capture row count and schema from the cached Explore output and return an
   `ExploreCacheReport`.
7. Cache the report itself in a small in-memory LRU keyed on the dataframe
   cache key, node id, source, and version, so reselecting the node does not
   re-materialise unchanged data.

Downstream graph edits do not invalidate the Explore dataframe cache entry.
Upstream node/config, edge, preamble, source file, model artifact, external
data, or Explore code changes invalidate it through the existing dataframe
cache key and input fingerprint logic.

## Job Lifecycle

Explore follows the modelling/optimiser job style rather than relying on the
synchronous preview route for full-data materialisation:

- `POST /api/explore/run` starts or returns a cached materialisation job for an
  Explore node.
- `GET /api/explore/status/{job_id}` returns progress and the cache report when
  complete.
- `POST /api/explore/cancel/{job_id}` cancels an in-flight job.

Starting a run is idempotent for the same cache key: a completed report returns
`status: "completed"` with the cached payload immediately. Starting a new job
for the same node and source supersedes the prior in-flight job for that family
via `CancellableJobRegistry.register_latest`.

## UI Layout

The right `NodePanel` shows Explore-specific panes:

- `Polars Code`: existing code-editor pattern with input-source bar, upstream
  column completions, `assign to df` hint, and generated `return df` footer.
- `Overview`, `Relationships`, `Charts`, `Export`: empty v1 panes reserved for
  follow-up EDA controls.

The lower panel uses the shared `PreviewPanelFrame` also used by dataframe,
modelling, and optimiser previews:

- top bar with node icon, node label, optional status metadata, actions, and
  shared collapse/top-expand controls;
- tab bar for `Preview`, `Overview`, `Relationships`, and `Charts`;
- `Preview` embeds the shared `DataPreview` table after Explore code;
- `Overview`, `Relationships`, and `Charts` are empty placeholders in v1;
- `Process & cache full data` and `Cancel` actions run against the full
  post-code Explore dataframe.

Right-panel and lower-panel selected panes are remembered independently per
Explore node.

## Test Plan

Backend:

- parser and codegen round-trip `@pipeline.explore()` with descriptions, graph
  position, and optional Polars code (`tests/test_parser.py`,
  `tests/test_codegen_builders.py`);
- graph-shape contract rejects Explore with 0 or more than 1 incoming edge, or
  any outgoing edge, at parse, codegen, and execute time
  (`tests/test_graph_shape_contracts.py`);
- preview tolerates disconnected Explore nodes outside the target lineage and
  returns a node-level error when the disconnected Explore is the target
  (`tests/test_graph_shape_contracts.py`, `tests/test_bugfixes.py`);
- Explore preview executes Explore Polars code before returning rows
  (`tests/test_bugfixes.py`);
- `/api/explore/run` materialises the Explore node output through
  `DataFrameExecutionCache` and returns row/column counts and the dataframe
  cache key (`tests/test_explore_routes.py`);
- repeated runs with the same graph and source reuse both the dataframe cache
  and the typed report cache;
- downstream-only edits do not invalidate the Explore dataframe cache key;
- cancel interrupts a mid-flight materialisation;
- the Explore JobStore prefix returns a singleton distinct from training and
  optimiser (`tests/test_job_store.py`).

Frontend:

- selecting an Explore node opens `ExplorePreview` in the lower panel
  (`App.integration.test.tsx`);
- the right NodePanel shows Explore panes, remembers the active pane by node,
  and renders `ExploreCodeEditor` for the `Polars Code` pane
  (`NodePanel.test.tsx`, `useUIStore.test.ts`);
- `ExploreCodeEditor` reuses the standard code editor contract and passes
  upstream columns into completions (`ExploreCodeEditor.test.tsx`);
- `ExplorePreview` renders the shared frame, lower-panel tabs, the embedded
  dataframe preview, and cache run/cancel actions (`ExplorePreview.test.tsx`);
- shared preview panel controls are covered for default sizing, node icons,
  collapse, top expansion, full-height collapse, and restore behavior
  (`PreviewPanelFrame.test.tsx`);
- API contract tests reject malformed `ExploreCacheReport` payloads
  (`guards.contract.test.ts`, `client.contract.test.ts`).

## Future Scope

Follow-up EDA work should populate the scaffolding in this order:

1. Overview: quality checks, schema summaries, nulls, cardinality, range checks,
   duplicate/key checks, and configurable inclusion checkboxes.
2. Relationships: correlations, associations, leakage/key warnings, and target
   relationships once target selection exists.
3. Charts: user-built chart definitions persisted in Explore config and
   rendered from cached or preview data as appropriate.
4. Export: report/export workflows for selected panes, charts, and cached
   summaries.

The central constraint for all future panes is that analysis should reuse the
post-code Explore dataset and avoid repeated full reprocessing unless upstream
inputs or Explore code change.
