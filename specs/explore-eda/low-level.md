# Explore EDA — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/routes/explore.py` | FastAPI router (`/api/explore`): thin Explore materialisation and `/pivots` run/status/cancel/member endpoints. Graph-bearing requests share `flatten_graph`, `_ensure_source_file`, and `_validate_runtime_input_paths` before service delegation. |
| `src/haute/routes/_explore_service.py` | Core service: cache-key derivation (`ExploreCacheSpec`), background job execution (`_run_job`, `_materialise_and_summarise`), and all statistics/summary computation (`_build_frame_stats`, `_build_data_quality_summary`, `_build_categorical_summary`, `_build_overview_summary`). |
| `src/haute/_explore_cache.py` | Project-local durable Explore generations: strict metadata/current-report validation, leased current/stale reads, two-phase atomic publication, and restoration into the process dataframe execution cache. |
| `src/haute/routes/_pivot_service.py` | Pivot service: requires the derived Explore dataframe-cache entry, validates fields/aggregations, applies typed exact-member filters, enforces cardinality before aggregation, runs latest-wins admitted jobs, and caches typed matrices by calculation identity. |
| `src/haute/_cache.py` | Dataframe execution-cache invariant owned by [caching](../caching/low-level.md) and used by the execution path: the materialised Explore dataframe is reused independently of the in-process report cache. |
| `src/haute/_polars_utils.py` | [io-layer](../io-layer/low-level.md)-owned `cancellable_streaming_collect` primitive consumed by Explore so a cancelled analysis interrupts its in-flight native Polars query. |
| `src/haute/_column_summary.py` | Dtype facts shared with the [assistant](../assistant/low-level.md)'s value profiles: `is_unhashable_dtype` (the distinct-count gate), the reserved count-field alias `CATEGORICAL_COUNT_FIELD`, and `json_safe_scalar`. Explore's display formatting stays local to the service; only the facts that a second summariser would otherwise have to rediscover as production failures live here. |
| `src/haute/_explore_overview.py` | Standalone validator for the Explore node's `overview` config dict (`validate_explore_overview`, `EXPLORE_OVERVIEW_TOGGLE_KEYS`). Imported by codegen (`_codegen_builders.py`) and the parser (`_config_builder.py`), not by the service or route module. |
| `src/haute/_explore_charts.py` | Standalone v0-to-v1 migration and strict validator for ordered PivotChart cards, nested mappings/styles/axes/legend, ids/names, finite bounds, orientation and stack-normalisation defaults, card-wide stack-group consistency, and simple-literal future fields. |
| `src/haute/_explore_pivots.py` | Standalone v0-to-v1 migration and strict validator for ordered Explore pivot cards, placements, typed members, supported aggregations/options, unique ids/names, and simple-literal future fields. |
| `src/haute/schemas.py` | Shared Explore API/report contracts owned by [server-api](../server-api/low-level.md): existing cache-report contracts plus pivot run/status/member requests, typed failures, member/path/value/cell structures, and result matrices. |

## Key types and data structures

- **`ExploreCacheSpec`** (`_explore_service.py`, frozen dataclass) — the resolved identity of one
  Explore run: `node_id`, `upstream_node_id`, `source`, the
  `execution_facade.DataFrameExecutionCacheRequest`, the derived `dataframe_cache_key`, the
  derived `report_cache_key`, and a `family_key` tuple (`("explore", source_file, node_id,
  source)`) used to detect superseding runs of "the same" Explore node.
- **`ExploreFrameStats`** (`_explore_service.py`, frozen dataclass) — the intermediate result of
  one batched aggregation: `row_count`, `columns: list[ExploreColumnStat]`, and
  `overview_summary: ExploreOverviewSummary`.
- **`ExploreColumnStat`** (`schemas.py`) — per-column stats. `distinct_count` is `None` exactly
  when the dtype is unhashable (`pl.Object`, per `_UNHASHABLE_DTYPES`); otherwise it counts only
  valid (non-null, non-NaN) values — `n_unique()` counts the null bucket and the NaN bucket each as
  one distinct value, so column-stat assembly subtracts one for each bucket that is actually
  present before storing it. Numeric-only fields (`p25_value`, `median_value`, `mean_value`,
  `p75_value`, `std_value`, `zero_count`, `negative_count`) default to `None` and are only
  populated when `dtype.is_numeric()`. `nan_count` is narrower still — `None` unless the dtype is
  float (`Float32`/`Float64`, per `_is_float_dtype`), since `is_nan()` raises against a non-float
  numeric column and NaN cannot occur in an integer column at all.
- **`ExploreOverviewSummary`** (`schemas.py`) — `data_quality: ExploreDataQualitySummary` plus
  `categorical_summary: list[ExploreCategoricalColumnProfile]`, one profile per non-numeric
  column that has a schema stat.
- **`ExploreCacheReport`** (`schemas.py`) — the full response payload: identity fields
  (`node_id`, `upstream_node_id`, `source`, `dataframe_cache_key`), `row_count`, `column_count`,
  `columns`, `overview_summary`, `generated_at` (epoch seconds), and `execution_metrics`
  (attached after materialisation, not during `_build_frame_stats`).
- **`ExploreCacheSnapshotResponse`** (`schemas.py`) - inspection result with a closed
  `state: "missing" | "current" | "stale"`, a user-facing message, and a report only for a
  current generation. Inspection never starts a job.
- **Durable Explore generation** (`_explore_cache.py`) - one immutable generation directory
  containing `data.parquet` and `meta.json`, selected by an atomically replaced `current.json`
  pointer under `.haute_cache/explore/<family-digest>`. Metadata binds the family digest, exact
  report/dataframe cache keys, typed report, and validated Parquet size/schema/counts. A family is
  the pipeline source file, Explore node id, and active source; it retains one selected generation,
  with the previous generation retired only after the new pointer is committed and its final
  reader lease is released. Generation retirement is best-effort housekeeping after commit and
  therefore cannot change a successfully published job into an error.
- **`EXPLORE_OVERVIEW_TOGGLE_KEYS`** (`_explore_overview.py`) — frozenset of the five known
  overview card keys: `dataset_snapshot`, `data_quality`, `numeric_summary`,
  `categorical_summary`, `schema`. These must map to `bool`; any other key must map to a
  "round-trippable" value per `_is_round_trippable_overview_value` (recursively: `None`, `str`,
  `bool`, `int`, finite `float`, or `list`/`dict` of the same, with dict keys required to be
  `str`).
- **`ExploreChartConfig`** (`_types.py`) — one persisted version-1 ComboChart with stable
  id/name/enabled/source-pivot linkage, chart `orientation` (`"vertical"`/`"horizontal"`), Rows
  category settings, ordered Value encodings and exact
  series overrides, primary/secondary axes, and legend. Style mappings contain only closed mark,
  axis, number-format, colour, stack (`stack_group` plus Boolean `stack_normalize`), marker, and
  label values.
- **`ExplorePivotConfig`** (`_types.py`) — version-1 persisted card with `id`, `name`,
  `enabled`, ordered Filter/Columns/Rows/Values placements, and grand-total options. Each
  placement owns a stable id. Filter members are typed scalars and Value placements add one of
  the seven supported aggregations plus a presentation-only display name.
- **`PivotCalculationSpec`** (`_pivot_service.py`, frozen dataclass) — resolved Explore cache
  request/key, validated v1 pivot, calculation hash, result-cache key, and latest-wins family key
  `("explore_pivot", source_file, node_id, source, pivot_id)`.
- **`ExplorePivotResult`** (`schemas.py`) — result schema version, Explore/pivot/source/cache and
  calculation identities, ordered row/column field names, stable value identities, typed row and
  column paths with total markers, typed cells, warnings, generation time, and execution metrics.
  Result values never carry card/value display names; the UI joins those from current persisted
  presentation config so rename-only reuse cannot surface stale labels.
- **`EXPLORE_CACHE_VERSION = 5`** and **`EXPLORE_REPORT_CACHE_MAX_ENTRIES = 16`** — module
  constants in `_explore_service.py`. The version is folded into `report_cache_key` so an
  incompatible report schema never collides with old cached payloads; the LRU is capped at 16
  entries (`LRUCache`, from `caching`). Bumped from 3 to 4 when categorical truncation began
  following display-label groups rather than raw-value cardinality; v3 already added NaN counts and
  `distinct_count` was changed to count valid values only — both change what a cached report
  computes for an *unchanged* underlying dataframe, so a v2 report would otherwise be served with
  stale distinct counts and no NaN data under an identical dataframe cache key.

## Pivot calculation invariants

- `EXPLORE_PIVOT_RESULT_VERSION = 1`; limits are `MAX_ROW_GROUPS = 500`,
  `MAX_COLUMN_GROUPS = 100`, `MAX_DISPLAY_CELLS = 50_000`, and
  `MAX_FILTER_MEMBERS = 500`. Cardinalities are collected first under the admitted context. The
  displayed-cell check includes each requested total path and uses `max(len(values), 1)` so even
  an unconfigured layout has a bounded shape.
- `ExploreService.prepare_spec()` is the sole derivation of the Explore dataframe cache identity.
  `PivotService` consumes its request/key and calls `request.cache.get/scan`; it does not invoke
  graph execution. Cache eviction between admission and scan becomes a typed local
  `cache_required` failure, never fresh execution.
- Calculation canonicalisation includes only ordered filter field/member keys, ordered row and
  column fields plus their effective Row directions, Value placement ids/fields/aggregations plus
  the selected Value direction, total options, dataframe key, and result schema version. A
  `sort_by` selection whose effective ordering is unchanged reuses the same key. It excludes
  card name/enabled, Value display names, Value colour scales, and all unknown presentation
  fields. The in-process result LRU stores at most 32 matrices.
- Aggregation aliases are derived from stable Value placement ids rather than field names.
  Numeric operations first map floating NaN to null. `sum`/`average`/`median` require numeric
  fields; `min`/`max` also accept supported scalar non-numeric fields. Empty aggregates return
  null, while `count` and `distinct_count` return integer zero. Result normalisation preserves
  finite JSON scalar types, renders Decimal exactly, renders integers beyond JavaScript's exact
  range (|value| > 2^53 − 1) as canonical decimal strings so a JS client cannot silently round
  them, uses ISO strings for date/time values, lossily decodes Binary as UTF-8, and renders
  Duration with `str(timedelta)`. Row and column grand totals are explicit paths assembled from
  the same filtered frame.
- `options.sort_by` is null or the placement id of exactly one Row/Value. A missing field on an
  older v1 card derives the active Value id when one `sort_rows` direction exists, otherwise null.
  Row paths are compared level-by-level: only a selected Row uses its persisted `sort` direction,
  every other level is ascending, and null/NaN always remain last. When a Value is selected, the service also aggregates that
  Value by the complete Rows path independently of the Columns layout, orders ordinary paths by
  the resulting scalar, and falls back to ascending Row labels for ties. This auxiliary aggregate
  is reused for a displayed row-grand-total column when present; it is never exposed as a hidden
  cell when totals are disabled. A selected Value must carry the sole non-none `sort_rows`
  direction; Row/default sorting requires every Value direction to be none.
- A typed member key uses closed kinds `null|string|boolean|integer|float|nan|date|datetime|time|decimal`.
  Integer and decimal payloads are canonical decimal strings, temporal payloads are ISO strings,
  finite floats are JSON numbers, and null/NaN carry `value: null`. The members endpoint returns
  a display label and count beside the key, sorted by count descending then typed canonical key.
- Explore materialisation and pivot calculation share the Explore job-store namespace, and every
  job record carries its owning kind: `kind: "explore"` for materialisation jobs and
  `kind: "pivot"` for pivot jobs. Each service's status and cancel reject a job id whose kind it
  does not own with a 404, so an Explore cancel can never mark a pivot job cancelled without
  signalling its pivot cancellation token, and vice versa.

## PivotChart adapter invariants

- `frontend/src/panels/explore/chartConfig.ts` mirrors the backend v0/v1 validator (including
  orientation and stack-normalisation defaults, any-mark stacking, card-wide
  stack-group consistency, and the secondary axis's Boolean `enabled` — defaulting to `true`
  when absent, materialised into the parsed card, and rejected when disabled while any style
  is assigned to the secondary axis) and exposes
  `setSecondaryAxisEnabled` (disabling atomically moves every secondary-assigned style to the
  primary axis, clearing its stack membership per the axis-change rule, in the same commit;
  enabling changes only the flag) alongside
  deterministic chart creation, source resolution, dependent-chart lookup, presets
  (`combo`, `clustered_columns`, `stacked_columns`, and `hundred_percent_stacked_columns` —
  Combo leads as the general category and default, matching Excel's Combo: applying it seeds
  the classic starting
  arrangement of columns with the last Value as an ungrouped primary line, from which the
  per-Value chart-type and axis controls compose any mixed arrangement, and
  `seedValueEncodings` seeds the same Combo arrangement when a source pivot is selected (a
  single Value seeds one plain column); the 100% preset also
  sets the
  primary axis number format to `percent`, every other preset resets a `percent` primary to
  `inherit` while other formats survive, and preset application preserves chart orientation),
  preset detection (`detectChartPreset`: a pure predicate over the ordered Value encodings'
  `(mark, axis, stack_group, stack_normalize)` projection only — ids, colours, markers, and
  data labels never participate, and stack-group names are compared solely for shared-vs-null
  membership so a renamed group detects identically. Predicates: `clustered_columns` = every
  encoding column/primary/ungrouped; `stacked_columns` = every encoding column/primary sharing
  one group with normalize false; `hundred_percent_stacked_columns` = the same with normalize
  true. Everything else — lines, mixed marks, area marks, secondary-axis assignments, or an
  empty encoding list — is the general `combo` category; detection is total over `ChartPreset`
  with no separate custom state. On a single-Value chart applying `combo` yields one column
  and therefore detects as `clustered_columns`), canonical
  typed series-key helpers, a series-key display helper (`exploreChartSeriesLabel`: decodes a
  canonical series key to "column path › … · Value display name" against the current pivot,
  requiring the exact canonical version-1 JSON shape; persisted overrides materialise its
  canonical serialization, while malformed persisted or direct-helper input is rejected clearly,
  so no raw key or internal id is rendered), and value-encoding reconciliation
  (`reconcileValueEncodings`: appends one seeded default encoding per pivot Value without one,
  in pivot order; each id is the first unused `encoding_N` counting up from 1 against the
  card-wide nested-id set spanning both Value encodings and exact overrides — e.g. with
  encoding ids `encoding_1`/`encoding_3` and an override id `encoding_2`, two seeded
  encodings receive `encoding_4` then `encoding_5`; returns its input
  reference unchanged when the chart is already complete and never mutates arguments). Source
  resolution never falls back from null/missing ids.
- `chartData.ts` is a pure adapter over a guarded pivot result plus parsed pivot/chart config. It
  checks source/result identities, explicit Value encodings (rejecting an unreconciled chart),
  row-only grand-total inclusion (`include_grand_total` admits row grand-total paths only;
  column grand-total paths are excluded unconditionally), numeric cells,
  hierarchy/label/cardinality limits, and applies matching exact overrides. After series
  assembly it normalises every stack group whose styles set `stack_normalize`: per category,
  each non-null cell becomes cell ÷ Σ|cells| over the group's non-null cells; if that direct
  sum overflows, it uses the equivalent max-magnitude-scaled denominator (range [-1, 1];
  nulls stay gaps and never enter the denominator), and a zero denominator renders every
  member's cell as a gap and appends one warning naming the category and group. Formatted text
  is derived after normalisation. Its output retains
  raw `number | null` points separately from formatted text and reports dormant overrides.
  `inherit` formatting is the General locale format: grouped `en-GB` with
  `maximumFractionDigits: 2` at magnitude ≥ 1, `maximumSignificantDigits: 4` for non-zero
  magnitude < 1, and the literal `0` at zero — e.g. `1234567.891` → `1,234,567.89`,
  `1234.5` → `1,234.5`, `12` → `12`, `0.12345` → `0.1235`, `-0.000123` → `-0.000123`,
  `0` → `0`.
- `chartOptions.ts` is the only renderer-option builder. It maps the adapter's closed data to
  ECharts Bar/Line series, primary/secondary axes, explicit stacks on any mark, safe text
  tooltips,
  deterministic token colours, axis bounds, legend, reduced motion, and zero-inclusive automatic
  column axes. Under `orientation: "horizontal"` the category axis moves to `yAxis`, the value
  axes to `xAxis`, series bind through `xAxisIndex` instead of `yAxisIndex`, and the
  many-categories data zoom follows the category axis; category label rotation applies to the
  category axis wherever it sits. Legends use a bounded scrolling layout instead of wrapping
  across the plot. The
  plot grid reserves space for every legend position and the title, and the secondary axis is
  hidden until at least one series uses it. It accepts no opaque persisted renderer objects or
  callbacks.
- `ComboChart.tsx` owns runtime initialisation, theme redraw, ResizeObserver sizing, deterministic
  disposal, a renderer-independent labelled summary region, and a toggleable semantic source-data
  table. The stable Haute summary remains available even when ECharts annotates its own nested SVG
  host. The ECharts runtime is imported only beneath the already-lazy Charts pane.
- `useExplorePivotActions.ts` owns the one Pivot run/status/cancel write lifecycle shared by the
  Pivots and Charts result panes; a chart never creates a parallel execution path.

## Control flow

### `POST /api/explore/cache-status`

1. The route applies the same graph flattening, source-file completion, and runtime-path
   validation as `/run`, then derives the canonical `ExploreCacheSpec` without executing nodes.
2. The service leases the selected durable family generation for the complete inspection/restore
   operation. No generation returns `missing`; a generation whose `report_cache_key` differs
   returns `stale`; an exact match is strictly validated, copied into the request's process-local
   `DataFrameExecutionCache` while still leased, and returned as `current` with its typed report.
3. If no durable generation exists, an exact report plus dataframe already present in the two
   process-local caches may satisfy `current`. A report alone is not a dataset cache hit.
4. Malformed pointers, metadata/report disagreement, missing Parquet, or Parquet metadata mismatch
   raise the durable-cache corruption error and fail the request; they never downgrade to
   `missing`. A generation with a different report cache key is stale and its old report payload
   is not parsed as the current schema; report-schema upgrades therefore remain re-cacheable.

### `POST /api/explore/run` (`routes/explore.py:run_explore` → `ExploreService.start`)

1. `flatten_graph(body.graph)` resolves any subgraph/composite references in the posted graph.
2. `_ensure_source_file(graph)` and `_validate_runtime_input_paths(graph)` (shared route
   helpers) validate the graph has a resolvable source file and that runtime input paths are
   legal, before any Explore-specific logic runs.
3. `ExploreService.start(body)`:
   a. `prepare_spec(body)` — see below — resolves node/upstream, builds the dataframe cache
      request, and derives both cache keys. Raises `HTTPException(400)` if the node is not
      Explore-typed or does not have exactly one parent.
   b. Unless `body.refresh` is true, require both the exact report and dataframe process-cache
      entries for an immediate in-process hit. If either is absent, inspect the durable family;
      an exact generation is restored into the dataframe cache and returned synchronously as
      `ExploreRunResponse(status="completed", cached=True, result=report)`.
   c. For `refresh: true`, bypass those hits, evict the exact process report/dataframe entries, and
      leave the selected durable generation untouched until a replacement succeeds.
   d. Otherwise create a job via `JobStore.create_job` with initial fields (`kind="explore"`,
      `status="running"`, `progress=0.0`, `node_id`, `upstream_node_id`, `source`,
      `analysis_key`).
   e. `CancellableJobRegistry.register_latest(spec.family_key, job_id)` returns a cancellation
      token and the previous job id (if any) for the same family. If a previous job existed, it
      is transitioned to `superseded` via `JobLifecycle.transition` (guarded by
      `expected_status="running"` — a no-op if it already finished).
   f. A daemon thread (`haute-explore-{job_id}`) runs `_run_job`; the route returns
      `ExploreRunResponse(status="started", job_id=job_id)` without waiting.

### `prepare_spec` (`ExploreService.prepare_spec`)

1. `find_typed_node(graph, body.node_id, NodeType.EXPLORE, "explore")` — raises 404 if the node
   is missing and 400 if it exists but is not Explore-typed.
2. Reads `graph.parents_of[node.id]`; raises `HTTPException(400)` unless there is exactly one
   parent.
3. `execution_facade.dataframe_graph_input_fingerprint(...)` computes an input fingerprint over
   the graph reachable to the Explore node, the source, etc.
4. `execution_facade.build_dataframe_execution_cache_request(...)` builds the
   `DataFrameExecutionCacheRequest` in the `"explore_dataset"` namespace, using
   `ExecutionProfile.EXPLORE_ANALYSIS`, contract enforcement enabled, and
   `body.streaming_chunk_size or
   DEFAULT_STREAMING_CHUNK_SIZE`.
5. `dataframe_key = dataframe_cache_request.keys_by_node[node.id].cache_key`.
6. `report_cache_key` is `"explore:v{EXPLORE_CACHE_VERSION}:{digest}"` where `digest` is
   `content_hash_bytes` of a canonical JSON encoding
   (`json.dumps(..., sort_keys=True, separators=(",", ":"), default=str)`) of
   `{dataframe_cache_key, node_id, source, version}`. Notably the `overview` config block is
   **not** part of this payload, which is what makes an overview-only config change reuse the
   cached report.

### `_run_job` (background thread)

1. `create_admitted_execution_context(operation="explore_cache", profile=EXPLORE_ANALYSIS,
   job_id, cancellation_token=token.execution_token)` — admission-gated context creation; raises
   `ExecutionAdmissionError` if the run cannot be admitted under current memory limits.
2. `bind_running_execution_metrics_publisher(store, job_id, execution_context)` streams live
   execution metrics into the job store while the job runs.
3. `_materialise_and_summarise(body, spec, job_id, execution_context)` — see below.
4. `execution_context.checkpoint(label="explore_before_store", node_id=spec.node_id)`.
5. The report is copied with `execution_metrics` attached
   (`ExecutionMetricsPayload.model_validate(execution_context.metrics_payload(status="completed"))`).
6. Once computation and terminal metrics are complete, the context releases its memory-admission
   reservation. While the exact dataframe entry is protected from eviction, the service copies
   and validates a new staged durable generation without selecting it.
7. `CancellableJobRegistry.latest_publication(job_id)` makes the latest-owner check indivisible
   from the short final publication. A cancelled or superseded owner discards its staging
   directory. The latest owner atomically selects the generation, stores the report in
   `self._report_cache`, and transitions the still-running job to `completed` while the guard is
   held; a replacement request therefore cannot interleave those three visible effects.
8. After commit, unselected generations without reader leases are retired best-effort. Cleanup
   failure is logged but does not invalidate the selected pointer or completed job.
9. `finally`: `execution_context.release_admission()` (idempotently, if a context was created) and
   `self._jobs.release(job_id)` always run, even on the exception paths below.

Exception handling (each maps to a distinct terminal status via `JobLifecycle.transition`, see
Error handling section): `ExecutionCancelledError` → `token.terminal_reason or "cancelled"`;
`ExecutionAdmissionError` / `ExecutionMemoryLimitExceededError` → `"memory_limited"`;
`ContractMismatchError` / `SchemaMismatchError` / `BoundedMemoryUnsupportedError` →
`"contract_error"`; any other `Exception` → `"error"` (logged via `logger.error(
"explore_cache_failed", ...)` with `exc_info=True`).

### `_materialise_and_summarise`

1. Updates job progress to `0.1` ("Executing Explore pipeline").
2. `_compile_preamble(body.graph.preamble or "", pipeline_dir=_pipeline_dir(body.graph))`
   (imported lazily from `haute.executor`).
3. `execution_facade.execute_lazy_graph(body.graph, _build_node_fn, target_node_id=spec.node_id,
   preamble_ns=preamble_ns or None, source=body.source, enforce_contracts=True,
   execution_context=execution_context, dataframe_cache_request=spec.dataframe_cache_request)` —
   executes the graph lazily up to (and including) the Explore node's own analysis code, reusing
   the dataframe cache when the request's cache key already has a hit. If that lineage contains a
   group-by, the lazy executor invokes graph-aware strategy planning before node execution so the
   boundary is admitted only when its source-derived estimate fits this job's
   `EXPLORE_ANALYSIS` admission headroom.
4. `lazy_outputs.get(spec.node_id)` must not be `None`; if it is,
   `ValueError(f"No data arrived at Explore node '{spec.node_id}'.")` is raised (caught by the
   generic `except Exception` branch in `_run_job`, i.e. surfaces as job status `error`).
5. Updates progress to `0.85` ("Reading cached schema"); `explore_lf.collect_schema()`.
6. `execution_context.stage("explore_frame_stats")` wraps `_build_frame_stats(explore_lf, schema,
   execution_context=execution_context)` — the single aggregation pass (see below).
7. Returns a populated `ExploreCacheReport` (without `execution_metrics`, which `_run_job` attaches
   afterward).

### `_build_frame_stats` — the single batched aggregation

Given `lf: pl.LazyFrame` and `schema: pl.Schema`:

1. Builds one `aggregations: list[pl.Expr]` list: `pl.len().alias("row_count")`, plus
   `pl.struct(all columns).n_unique().alias("unique_rows")` when the schema is non-empty and
   every dtype is hashable, then per column `name`/`dtype`:
   - `null_count().alias(f"null::{name}")` always.
   - `n_unique().alias(f"unique::{name}")` unless `_is_unhashable_dtype(dtype)`.
   - min/max (via `_min_max_column_expr`, casting text-like/boolean bases to `String`) when
     `_supports_min_max(dtype)` (numeric, temporal, boolean, or lexical text-like bases).
   - if `dtype.is_numeric()`: p25/median/mean/p75/std, plus `zero::{name}` and
     `negative::{name}` boolean-sum counts; additionally, if `_is_float_dtype(dtype)`:
     `is_nan().sum().alias(f"nan::{name}")` — `is_nan()` yields null (not true) for null rows, so
     `.sum()` counts only genuine NaN values, distinct from the null count.
   - `elif _has_categorical_value_counts(dtype)`: `_categorical_value_counts_expr(name, dtype)`
     — `value_counts(sort=True).struct.rename_fields(...).head(50).implode()` — aliased
     `categorical_values::{name}`, plus `n_unique()` over the same display-label expression,
     aliased `categorical_label_groups::{name}`.
   - text-like dtypes additionally aggregate min/mean/max display-label character length;
     temporal dtypes aggregate `max - min` as a duration-like span.
2. The shared I/O-layer helper
   `cancellable_streaming_collect(lf.select(aggregations), execution_context=execution_context)`
   checkpoints before starting native work, then calls
   `collect(engine="streaming", background=True)`, polls `InProcessQuery.fetch()` at a bounded
   interval, checkpoints between polls, and calls `query.cancel()` before re-raising a checkpoint
   failure. It is still exactly one native Polars collection for the entire frame.
3. Iterates columns again to build `ExploreColumnStat` entries from the single aggregate row,
   plus a `categorical_values_by_column` dict for columns with
   `_has_categorical_value_counts(dtype)`, parsed via `_parse_categorical_value_counts` (which
   sorts by descending count, then `value is None` last, then value ascending). `distinct_count`
   starts as the raw `unique::{name}` aggregate and is then decremented by 1 if `null_count > 0`
   and again by 1 if `nan_count` is truthy — `n_unique()` counts the null bucket and (for float
   columns) the NaN bucket each as one distinct group, and the stat's `distinct_count` is defined
   as valid-values-only.
4. For each hashable column, `unique_ratio` is valid distinct count divided by valid row count
   (rows minus null and float-NaN counts), or `None` when there are no valid rows. A distinct
   count above 50 sets `is_high_cardinality` only when the dtype base is text-like
   (`_TEXT_DTYPE_BASES`). `_is_identifier_candidate` additionally requires
   at least two rows, no missing/NaN values, one distinct value per row, and an id/key/uuid/guid
   name shape.
5. `row_count = int(aggregate_row["row_count"])`; `duplicate_row_count` is
   `row_count - unique_rows` when the whole-row expression was available, otherwise `None`.
6. Returns `ExploreFrameStats(row_count, columns, overview_summary=_build_overview_summary(...))`.

### Data-quality summary (`_build_data_quality_summary`)

Given `row_count`, `columns`, and the optional duplicate count, in this fixed order, each
condition appends at most one `ExploreDataQualityIssue` (so up to 7 issues total per report):

1. **Missing values** — any column with `null_count > 0`, sorted by descending null ratio then
   name. Severity is `"danger"` if any of those columns are ≥50% null
   (`mostly_null_columns`), else `"warning"`. Label counts all missing-value columns; detail
   names only the single worst column and its percentage (via `_percent_text`, which renders
   `"<1%"` for a nonzero ratio under 1%).
2. **NaN values** — any column with `(nan_count or 0) > 0`, sorted by descending NaN ratio then
   name; severity/label/detail follow the same `"danger"`-if-≥50%-else-`"warning"` pattern as
   missing values, with the label reading `"N numeric column(s) with NaN values"`. Computed and
   appended immediately after missing values, before the zero-heavy/constant checks below.
3. **Constant/single-value columns** — `distinct_count == 1 and null_count == 0 and not
   (nan_count or 0)`, excluding columns already flagged zero-heavy (see below) so a column is not
   double-counted between the two issues. Tightened from a looser `distinct_count <= 1 and
   null_count < row_count` rule (which allowed a column with some nulls alongside its one valid
   value to still count as constant): "constant" now means every row holds the same valid value,
   with zero tolerance for null or NaN rows — a column that is single-valued except for missing or
   NaN rows is reported under issue 1/2 instead (ruled 2026-07-16).
4. **Numeric columns with negatives** — any column with `negative_count > 0`, sorted by
   descending count; detail names only the top offender with its row count.
5. **Mostly-zero numeric columns** — `(row_count - null_count) > 0 and zero_count > 0 and
   zero_count / (row_count - null_count) >= 0.95`, sorted by descending zero count; this issue is
   computed and referenced (via `zero_heavy_names`) before the constant-column issue is appended,
   even though it is appended last.
6. **High-cardinality fields** — any text-like column whose valid distinct count exceeds 50;
   detail lists up to three names. This is a caution about bounded categorical display, not an
   error, so it never fires for numeric or temporal columns.
7. **Duplicate rows** — when exact whole-row distinct counting is available and
   `duplicate_row_count > 0`; danger at a duplicate ratio of at least 50%, warning below it.

`_names_text`/`_limited_names` cap the number of names listed in a detail string at 3
(`_SUMMARY_NAME_LIMIT`), joined with `", "`; `_plural` pluralises "column"/"columns" based on
count.

### Categorical summary (`_build_categorical_summary`)

For each `ExploreColumnStat` whose dtype (looked up in `schema`) is not numeric, builds one
`ExploreCategoricalColumnProfile`:

- `expandable = distinct_count not in (None, 0) and _has_categorical_value_counts(dtype) and
  bool(values)` — i.e. the column both qualifies for bounded value counts *and* actually has
  computed values (an all-null column with 0 rows would not be expandable).
- `values_truncated`: the `value_counts(...).head(50)` expression groups the display-label
  expression (including a null label) before clipping. The aggregation separately counts those
  exact label groups with `n_unique`, so truncation is true only when that count exceeds 50. This
  avoids false truncation when several raw `Binary` values lossily decode to one label; it is false
  when no value-count aggregation was computed (for example, `List`). `distinct_count` remains the
  analyst-facing raw-value count.
- `values` comes from the `values_by_column` dict built in `_build_frame_stats`; columns without
  an entry get `[]`.

## Edge cases and invariants

- **Object dtype**: excluded from `n_unique` entirely (`_is_unhashable_dtype`); `distinct_count`
  is `None`, never computed and never guessed.
- **All-null column**: min/max are `None` (Polars aggregate returns `None`); for numeric columns
  all of p25/median/mean/p75/std are `None` but `zero_count`/`negative_count` are `0`, not
  `None` — a numeric column's count fields are always present even when every value is null.
- **All-NaN float column**: `null_count` alone would read this column as fully populated (NaN is
  not null); `nan_count` catches it, and `distinct_count` reads `0` rather than `1` since the NaN
  bucket is subtracted out along with the null bucket (`test_build_frame_stats_reports_nan_counts_for_float_columns_only`,
  `test_build_frame_stats_distinct_count_excludes_nan_bucket`). The column is flagged under the
  NaN data-quality issue, not the constant-column issue
  (`test_build_frame_stats_all_nan_column_is_not_flagged_constant`).
- **A single valid value plus nulls or NaNs is not constant.** A column with exactly one distinct
  non-null value but any null or NaN rows fails the strict `null_count == 0 and not nan_count` gate
  and is reported only under the missing-values/NaN issue, never double-counted as constant
  (`test_build_frame_stats_single_valid_value_with_nan_is_not_constant`,
  `test_build_frame_stats_single_valid_value_with_nulls_is_not_constant`).
- **NaN counting is gated on float dtype, not `is_numeric()`.** `is_nan()` raises
  `InvalidOperationError` against a non-float numeric column (e.g. `Int64`); `_is_float_dtype`
  restricts the `nan::{name}` aggregation and the resulting `nan_count` field to
  `Float32`/`Float64` columns only, leaving it `None` for every other dtype including other
  numeric ones.
- **Binary columns with non-UTF-8 bytes**: `_categorical_value_label_expr` uses
  `map_elements(_lossy_decode_binary, ...)` instead of `cast(pl.String)`; undecodable bytes
  become `"�"` (the invariant under test in
  `test_build_frame_stats_survives_non_utf8_binary_column`).
- **Duration columns**: `map_elements(_format_duration, ...)` (i.e. `str(timedelta)`) instead of
  `cast`, because Polars cannot cast `Duration` to `String`; the same formatting is reused for
  min/max display via `_STRING_MIN_MAX_DTYPE_BASES` *not* including `Duration`, so Duration
  min/max go through the generic `_format_display_value(str(value))` path and happen to produce
  identical text to the value-count labels (documented as intentional, not incidental, in the
  module docstring of `_format_duration`).
- **Boolean columns**: cast to `String` for both min/max and value counts
  (`_STRING_MIN_MAX_DTYPE_BASES` includes `pl.Boolean`) so `"true"`/`"false"` display identically
  in the Schema and Categorical cards, instead of Python's `str(bool)` capitalisation.
- **High-cardinality columns**: value counts are capped at exactly 50
  (`_CATEGORICAL_VALUE_COUNT_LIMIT`) via `.head(50)`; `values_truncated` is `True` only when the
  separately aggregated display-label group count (including a null label) exceeds 50 (exactly 50
  display-label groups is not truncated —
  see `test_build_frame_stats_returns_all_values_for_exactly_50_categorical_groups`; exactly 50
  distinct non-null values *plus* nulls is 51 groups and *is* truncated — see
  `test_categorical_truncation_counts_null_bucket_as_a_group`). `expandable`/`values_truncated`
  are independent booleans — a column can be expandable without being truncated.
- **Nested/unsupported dtypes** (e.g. `List`): not numeric and not text-like, so
  `_has_categorical_value_counts` is `False`; `distinct_count` is still computed (nested types
  are hashable in Polars) but the profile has `expandable=False`, `values=[]`.
  `_column_kind` classifies any `dtype.is_nested()` as `"Nested"`.
  `_supports_categorical_value_counts` defines dtypes whose values have a stable direct display;
  `_has_categorical_value_counts` additionally excludes numeric and unhashable dtypes and is the
  single gate for both the aggregation and parse. `_build_categorical_summary` emits a profile for
  every non-numeric column, including unsupported nested types.
- **Column named `count`**: the aliasing scheme (`categorical_values::{name}`,
  `_CATEGORICAL_VALUE_FIELD`/`_CATEGORICAL_COUNT_FIELD` prefixed with `__haute_`) avoids
  colliding with a user column literally named `count` (regression test
  `test_build_frame_stats_categorical_value_counts_handle_count_column_name`).
- **Empty schema**: `_build_frame_stats` on a zero-column `LazyFrame`
  return `columns == []` without error.
- **Value truncation for display**: any min/max or categorical value longer than 80 characters
  (`_VALUE_DISPLAY_MAX_CHARS`) is clipped to exactly 80 characters plus a single `"…"` marker
  (`_VALUE_DISPLAY_TRUNCATION_MARKER`), so the returned string is always ≤81 characters.
- **Single aggregation guarantee**: `_build_frame_stats` performs exactly one
  `cancellable_streaming_collect` call regardless of the number of columns or how many need
  categorical value counts — asserted directly in tests by monkeypatching that helper and counting
  invocations, and by
  inspecting the query plan for absence of `UNION`/`CACHE` nodes (ruling out an unpivot-based
  implementation).
- **Explore display-config round-trip**: an explicit `False` overview toggle or disabled chart
  must be preserved through codegen → parse (not treated the same as an absent key); pivot cards
  retain ordered ids and future fields. Empty `overview: {}`, `pivots: []`, and `charts: []`
  values must not be emitted as decorator kwargs. Non-empty kwargs are emitted in stable
  overview-then-pivots-then-charts order.
- **Downstream-edit cache stability**: `dataframe_cache_key` is unchanged by edits to nodes
  downstream of the Explore node (verified by comparing `prepare_spec(...).dataframe_cache_key`
  across two graphs differing only in a downstream node's label) and by adding an `overview`,
  `pivots`, or `charts` block to the Explore node's own config (the display payload is not part of either
  cache key's input). It *is* changed by editing the Explore node's own analysis `code`.

## Error handling

- `HTTPException(status_code=400, ...)` — raised synchronously (not inside the background job)
  from `prepare_spec` when the target node has zero or multiple upstream parents, and from
  `find_typed_node` when the node is not Explore-typed. A missing node id returns HTTP 404. These
  surface directly as the HTTP response; no job is created.
- `ConfigError` (from `haute.errors`) — raised by the Explore display-config validators for
  structurally invalid overview/pivot/chart values, including malformed cards and duplicate ids.
  Raised during codegen/parse, not during the run/status/cancel routes.
- `ExecutionCancelledError`, `ExecutionAdmissionError`, `ExecutionMemoryLimitExceededError`,
  `PUBLIC_CONTRACT_ERROR_TYPES` (mapped with `contract_error_job_fields` to `contract_error`),
  `ContractMismatchError`, `SchemaMismatchError`, `BoundedMemoryUnsupportedError` — all caught
  inside `_run_job` and translated into a terminal job status with a message payload (see
  Control flow); none propagate out of the background thread, and none abort the process.
  `exc.to_payload()` is used (not `str(exc)` directly) for `ExecutionAdmissionError` and
  `ExecutionMemoryLimitExceededError` to get a structured message.
- Any other `Exception` — caught by the final `except Exception` clause in `_run_job`, logged via
  `logger.error("explore_cache_failed", job_id=job_id, error=str(exc), exc_info=True)`, and
  translated to job status `"error"`. This includes the `ValueError` raised by
  `_materialise_and_summarise` when no data arrives at the Explore node.
- `JobStore.require_job` (used by `status`/`cancel`) raises `HTTPException(404, "Job '{job_id}'
  not found")` directly on an unknown job id (verified by
  `test_explore_status_unknown_job_is_404`); this behaviour is inherited from the shared
  [background-jobs](../background-jobs/high-level.md) `JobStore`, not implemented in this
  component.

## Testing

- `tests/test_explore_routes.py` — the primary suite, exercising the service end-to-end through
  the FastAPI `TestClient`:
  - `test_explore_run_returns_cache_descriptor` / `test_explore_run_applies_node_polars_code_before_caching`
    — happy-path run, including that the Explore node's own analysis code executes before
    caching (row/column counts reflect the post-code frame).
  - `test_explore_reuses_completed_report_for_same_analysis_key` /
    `test_explore_reuses_typed_report_cache_without_reexecuting_sources` — report-cache hits
    return synchronously and skip `_materialise_and_summarise` entirely (asserted by
    monkeypatching it to raise on any call).
  - `test_explore_downstream_edits_do_not_invalidate_analysis_dataframe_cache` /
    `test_explore_display_config_does_not_invalidate_analysis_dataframe_cache` /
    `test_explore_code_config_change_invalidates_analysis_dataframe_cache` — pin the cache-key
    invalidation invariants directly via `prepare_spec(...).dataframe_cache_key` comparisons.
  - `test_explore_rejects_non_explore_node_before_execution` — 400 with a message containing
    `"is not a explore node"`.
  - `test_explore_cancel_stops_in_flight_job` — gates the Explore collect helper on a
    `threading.Event`
    to force a mid-flight cancel, then asserts the worker thread actually exits (not just that
    the status flips).
  - `test_explore_status_unknown_job_is_404`.
  - A block of column-stat regression tests (`test_cache_report_includes_one_column_stat_per_column`,
    `test_null_count_matches_input`, `test_distinct_count_matches_input`, `test_nan_count_matches_input`,
    `test_min_value_truncated_at_80_chars_with_ellipsis`,
    `test_all_null_column_has_none_min_max_values`, `test_column_order_matches_schema`) driven
    through the full `/api/explore/run` → poll path with an identity prep step, so per-column
    assertions are deterministic.
  - A large block of direct unit tests against `_build_frame_stats` (no
    HTTP layer) covering: Object vs. Struct distinct-count handling, empty schema, numeric
    profile fields, boolean min/max-vs-value-count casing consistency, all-null numeric columns,
    the full data-quality summary issue set and ordering, bounded categorical value counts
    (including exactly-50 and >50 truncation boundaries), unsupported (nested/list) categorical
    profiles, the `count`-named-column aliasing collision guard, Binary/Duration lenient
    formatting, and the single-batched-collect invariant (call-count assertion plus query-plan
    inspection for absence of `UNION`/`CACHE`). The three-way missingness split added: NaN counts
    populated only for float columns (`test_build_frame_stats_reports_nan_counts_for_float_columns_only`),
    the NaN data-quality issue and its danger/warning severity threshold
    (`test_build_frame_stats_flags_nan_columns_in_quality_summary`,
    `test_build_frame_stats_nan_issue_is_warning_below_half`), `distinct_count` excluding the null
    and NaN buckets (`test_build_frame_stats_distinct_count_excludes_null_bucket`,
    `test_build_frame_stats_distinct_count_excludes_nan_bucket`), the tightened constant-column
    rule rejecting a single valid value alongside nulls or NaNs
    (`test_build_frame_stats_single_valid_value_with_nan_is_not_constant`,
    `test_build_frame_stats_all_nan_column_is_not_flagged_constant`,
    `test_build_frame_stats_single_valid_value_with_nulls_is_not_constant`), and the categorical
    `values_truncated` display-label group count, including the null label
    (`test_categorical_truncation_counts_null_bucket_as_a_group`) and the no-false-truncation
    regressions for unsupported Lists and colliding Binary display labels.
  - `_clean_explore_state` autouse fixture snapshots/restores `_store.jobs` and clears
    `_explore_service._report_cache` around each test so report-cache and job-store state never
    leaks between tests.
- `tests/test_explore_round_trip.py` — exercises the Explore display validators indirectly
  through `graph_to_code` → `parse_pipeline_source`: overview toggle/unknown-key round trips,
  ordered pivot and enabled/disabled chart cards with future settings, and omission of empty
  `overview={}`/`pivots=[]`/`charts=[]` kwargs.
- `tests/test_explore_charts.py` — pins chart-container/card shape validation, required state,
  duplicate-id rejection, future simple-literal fields, order preservation, and detached output.
- `tests/test_explore_pivots.py` — pins pivot-container/card shape validation, duplicate-id
  rejection, future simple-literal fields, order preservation, and detached output.
- `tests/test_explore_pivot_routes.py` exercises cache-required behaviour, typed filters,
  bounded aggregation/cardinality, sorting/totals, latest-wins job lifecycle, member lookup,
  export, and JSON-safe scalar results including Binary and Duration min/max values.
