# Explore Node Spec

Status: shipped (v1 — dataset caching only)
Owner: Explore node workstream
Last updated: 2026-05-18

## Current Scope

The Explore node in v1 has exactly one responsibility: **materialise the
upstream dataset into the dataframe execution cache**. It is the foundation for
future analysis work, not the analysis itself.

What ships:

- `explore` node type, available in the palette, rendered with the explore
  accent colour and a search icon.
- `@pipeline.explore` decorator, configuration-free, that round-trips cleanly
  through parser and codegen.
- Graph-shape contract: explore nodes must have exactly one incoming edge and
  zero outgoing edges. Parser, codegen, and executor all enforce it so
  malformed graphs fail with a typed `ParseError` rather than a Polars
  `TypeError`.
- `/api/explore/run`, `/api/explore/status/{job_id}`, `/api/explore/cancel/{job_id}`
  endpoints backed by a background-job worker.
- Lightweight typed `ExploreCacheReport` response: node id, upstream node id,
  source, dataframe cache key, row count, column count, generated timestamp,
  and execution metrics.
- Lower-panel `ExplorePreview` component with a `Process & cache full data` /
  `Cancel` control. The body is intentionally empty in v1 — it is reserved for
  the upcoming EDA surface (overview, charts, relationships) that will render
  against the cached dataframe.
- Right-panel tab scaffolding (`Overview`, `Relationships`, `Charts`,
  `Export`) on Explore nodes with per-node pane memory. The pane bodies are
  empty in v1; the chrome is in place so the configuration editors can be
  added without rewiring the panel layout.

What does **not** ship in v1 (deliberately out of scope):

- Quality, profile, warnings, relationships, or segments sections.
- Charts, heatmaps, or any dense diagnostic tables.
- Configuration knobs (target column, grouping, key columns, thresholds, row
  budget, included columns).
- A right-panel Explore configuration editor — the right panel intentionally
  has no Explore-specific UI in v1.

These belong in subsequent versions, building on the cached dataset that v1
materialises.

## Approach

The Explore node is an analysis-only sink in the visual graph:

- `maxInputs: 1`
- no output handle
- no side effects beyond the cached dataframe

Users can branch an Explore node off any intermediate pipeline step to cache
that point in the data without changing the main modelling or rating path.

### Codegen, Parser, and Deploy Semantics

Haute's Python file remains the source of truth, so Explore nodes round-trip
through generated pipeline code rather than living only in frontend state.

The persisted representation is a no-kwargs decorator:

```python
@pipeline.explore()
def data_health(df: pl.LazyFrame) -> pl.LazyFrame:
    """Optional user-authored description."""
    return df
```

Key semantics:

- parser and codegen preserve the node, description, and edges;
- the generated function returns its input unchanged so the file is valid
  Python and the decorator can be inspected in code review;
- the visual graph does not expose an output handle, so Explore cannot be
  connected into a production scoring path through normal UI operations;
- deploy bundles prune nodes that are not ancestors of an output naturally —
  the graph-shape contract guarantees Explore can never be such an ancestor;
- if a malformed source file makes an Explore node an ancestor of a production
  output, parse-time validation raises `ParseError` before execution starts.

### Execution and Caching

The Explore execution path:

1. Validate that the selected Explore node exists, has node type `explore`,
   and has exactly one upstream parent.
2. Compile the pipeline preamble using the same executor helper used by
   training and optimiser.
3. Build a `DataFrameExecutionCacheRequest` for the upstream parent node
   (not the Explore node itself).
4. Execute the graph lazily to that parent with the current source and the
   `ExecutionProfile.EXPLORE_ANALYSIS` profile.
5. Materialise the full parent output through `DataFrameExecutionCache`;
   cache hits return a parquet scan without rerunning upstream nodes.
6. Capture the row count + schema and return an `ExploreCacheReport`.
7. Cache the report itself in a small in-memory LRU keyed on the dataframe
   cache key + node id + source + version, so reselecting the node does not
   re-materialise unchanged data.

Downstream graph edits do not invalidate the upstream dataframe cache entry.
Upstream node/config, edge, preamble, source file, model artifact, or external
data changes invalidate it through the existing dataframe cache key and input
fingerprint logic.

### Job Lifecycle

Explore follows the modelling/optimiser job style rather than the synchronous
preview route:

- `POST /api/explore/run` — starts (or returns a cached) materialisation job
  for an explore node.
- `GET /api/explore/status/{job_id}` — returns progress and the cache report
  when complete.
- `POST /api/explore/cancel/{job_id}` — cancels an in-flight job.

Starting a run is idempotent for the same cache key: a completed report
returns `status: "completed"` with the cached payload immediately. Starting a
new job for the same node + source supersedes (cancels) the prior in-flight
job for that family via `CancellableJobRegistry.register_latest`.

### UI Layout

The lower panel hosts the `ExplorePreview` cache-status view:

- header with node label, source, and current cache status;
- progress bar while materialisation runs;
- run/cancel button that submits or aborts the cache job;
- status table showing row count, column count, source, cached-at time, and
  cache key when a report is available.

The right `NodePanel` shows the standard node header for an Explore node but
no node-specific editor — v1 has no configuration to surface. The columns tab
is hidden (Explore has no outputs).

## Test Plan

Backend:

- parser and codegen round-trip `@pipeline.explore()` with descriptions and
  graph position (`tests/test_parser.py`, `tests/test_codegen_builders.py`);
- graph-shape contract rejects Explore with 0 or >1 incoming edges, or any
  outgoing edge, at parse, codegen, and execute time
  (`tests/test_graph_shape_contracts.py`);
- preview tolerates disconnected Explore nodes that are not in the target's
  lineage (`tests/test_graph_shape_contracts.py`, `tests/test_bugfixes.py`);
- `/api/explore/run` materialises the parent via `DataFrameExecutionCache`
  and returns a cache descriptor with row/column counts and the cache key
  (`tests/test_explore_routes.py`);
- repeated runs with the same graph + source reuse both the upstream
  dataframe cache and the typed report cache;
- downstream-only edits do not invalidate the upstream dataframe cache key;
- cancel actually interrupts a mid-flight materialisation;
- the explore JobStore prefix returns a singleton distinct from training and
  optimiser (`tests/test_job_store.py`).

Frontend:

- selecting an Explore node opens the lower `ExplorePreview` instead of the
  generic `DataPreview` (`App.integration.test.tsx`);
- the right NodePanel hides config + columns tabs and the refresh button for
  Explore nodes (`NodePanel.test.tsx`);
- `ExplorePreview` renders the cache report (row/column count, cache key)
  after a cached or completed response (`ExplorePreview.test.tsx`);
- API contract tests reject malformed `ExploreCacheReport` payloads
  (`guards.contract.test.ts`, `client.contract.test.ts`).

## Future Scope

These are deliberately deferred to follow-up work, once the v1 cache layer is
stable:

- structured analysis sections (overview metrics beyond row/column counts,
  data quality, column profiles, warnings, relationships, segments);
- distribution and correlation visualisations in the lower panel;
- right-panel configuration controls (target column, grouping, key columns,
  weight column, row budget, thresholds, included columns);
- target-aware bivariate diagnostics and segment comparison;
- artifact persistence for audit trails.

The full analysis design (sections, status semantics, sampling rules,
relationship measures) is intentionally not specified in this v1 document.
Those decisions will be made when v2 begins, against the constraints that the
cache layer surfaces in practice.
