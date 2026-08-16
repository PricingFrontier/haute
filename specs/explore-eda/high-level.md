# Explore EDA — High-Level Specification

## Purpose

The Explore node is a terminal, analysis-only branch that a pipeline author can attach to any
intermediate step in a Haute graph to inspect the full dataframe at that point. It exists so
analysts can materialise and profile a dataset — row/column counts, per-column schema stats,
data-quality issues, and bounded categorical value counts — without that inspection work
becoming part of the production scoring path. This component owns the backend half of that
workflow: validating the node's own `overview`, `pivots`, and `charts` display-config blocks, running the
materialisation job, and computing the summary statistics returned to the UI.

## Scope

In scope:

- Starting, polling, and cancelling a background job that materialises an Explore node's
  upstream dataframe (`POST /api/explore/run`, `GET /api/explore/status/{job_id}`,
  `POST /api/explore/cancel/{job_id}`).
- Computing the `ExploreCacheReport`: row count, column count, per-column schema stats
  (`ExploreColumnStat`), a data-quality summary, per-column bounded categorical value counts,
  text/temporal cues, cardinality flags, and an exact duplicate-row count when every column is
  hashable.
- Caching both the materialised dataframe (via the dataframe execution cache) and the typed
  report object (via a small in-process LRU) so repeated requests for the same analysis avoid
  recomputation.
- Validating the `overview` config dict attached to an Explore node — the set of toggle cards
  (`dataset_snapshot`, `data_quality`, `numeric_summary`, `categorical_summary`, `schema`) a user
  has enabled, plus round-trip-safe storage of any unrecognised keys.
- Validating the ordered, versioned `charts` config list attached to an Explore node. Every
  ComboChart card is complete version 1 — there is no legacy migration, and a card without
  `version: 1` is rejected. Every card has a unique id and
  case-insensitively unique name, a nullable stable pivot id, typed category/Value/series
  mappings, two typed numeric axes, and a typed legend; unknown simple-literal fields remain
  round-trippable at every supported nesting level.
- Validating the ordered, versioned `pivots` config list attached to an Explore node. Every card
  is complete version 1 — there is no legacy migration, and a card without `version: 1` is
  rejected. Every card has a unique id,
  case-insensitively unique name, visibility state, typed Filter/Columns/Rows/Values placements,
  and row/column grand-total options; unknown simple-literal fields remain round-trippable.
- Starting, polling, and cancelling pivot calculations over an existing Explore dataframe cache
  (`POST /api/explore/pivots/run`, `GET /api/explore/pivots/status/{job_id}`,
  `POST /api/explore/pivots/cancel/{job_id}`), and listing exact filter members from that cache
  (`POST /api/explore/pivots/members`).
- Producing a versioned, typed pivot matrix whose stable row paths, column paths, value identities,
  cells, grand-total markers, warnings, and execution metrics can feed both pivot tables and
  PivotCharts without another aggregation path.
- Resolving PivotChart references only against pivots on the same Explore node. A null source is
  an editable draft and an unknown id is a broken reference; neither silently selects another
  pivot. Pivot visibility does not affect source eligibility, while deletion is refused when a
  chart still references that pivot.

Out of scope (owned elsewhere):

- Executing the pipeline graph up to the Explore node, contract enforcement, and lazy-frame
  caching mechanics — see [execution-engine](../execution-engine/high-level.md).
- The on-disk/parquet dataframe cache and its key/fingerprint logic — see
  [caching](../caching/high-level.md).
- Generic job-store, job-lifecycle, and cancellation-registry mechanics shared with other
  long-running routes (modelling, optimiser) — see
  [background-jobs](../background-jobs/high-level.md).
- Parsing and code-generation of the `@pipeline.explore()` decorator and its
  `overview=`/`pivots=`/`charts=` kwargs into/out of pipeline source files — see
  [codegen](../codegen/high-level.md) and
  [expression-parsing](../expression-parsing/high-level.md) (the parser/codegen call sites live
  in `_codegen_builders.py` and `_config_builder.py`, which import the validators from
  `haute._explore_overview`, `haute._explore_pivots`, and `haute._explore_charts`).
- Rendering Explore views in the UI — see
  [frontend-preview-explore](../frontend-preview-explore/high-level.md).

## Behaviour

- A client starts a run by posting a graph, an Explore `node_id`, and a `source` ("live" or a
  named preview source). The Explore node in that graph must have exactly one upstream parent;
  otherwise the request fails immediately with an HTTP 400 before any execution starts.
- If a report for the same analysis (same graph shape reachable from the Explore node, same
  Explore code/config, same source, same report schema version) is already cached, the run
  request returns `status: "completed"` with the cached report synchronously — no job is
  created.
- Completed Explore datasets are durable project-local cache generations. The Parquet dataset and
  its typed report live under the project's ignored `.haute_cache/explore` directory, so closing
  the browser or restarting the local Haute backend does not discard a valid cache. Reopening an
  Explore node can inspect and restore that exact generation into the process execution cache
  without executing the graph again.
- `POST /api/explore/cache-status` compares the current graph/source identity with the latest
  durable generation for that Explore node/source and returns exactly `missing`, `current`, or
  `stale`. `current` includes the report and makes the dataframe immediately available to pivots;
  `stale` means a retained generation exists for the same cache family but its exact analysis
  identity differs. A corrupt or internally inconsistent durable generation fails explicitly; it
  is never presented as a usable hit or silently replaced.
- A run request with `refresh: true` is an explicit re-cache. It bypasses both report and durable
  cache hits and invalidates the matching process-local dataframe entry before execution, while
  retaining the last durable generation until the replacement has been completely and atomically
  published. A failed or cancelled refresh therefore leaves the previous generation available
  and stale/current according to its identity.
- Otherwise a job is created and runs in a background thread. The client polls
  `GET /api/explore/status/{job_id}` until the job reaches a terminal status
  (`completed`, `error`, `cancelled`, `superseded`, `memory_limited`,
  `contract_error`).
- A group-by in the lineage feeding the Explore node is admitted as part of this explicit
  full-frame cache materialisation only when its source-derived peak-memory estimate fits the
  admitted `EXPLORE_ANALYSIS` headroom. Missing estimates or insufficient headroom fail with the
  existing typed execution-strategy error; Explore never substitutes a partial aggregation or a
  generic chunked execution.
- Starting a new run for the same Explore node/source while a previous run is still in flight
  supersedes the older job; the older job's status transitions to `superseded`.
- `POST /api/explore/cancel/{job_id}` interrupts an in-flight materialisation (not just a status
  flip): Explore starts its statistics aggregation as a Polars streaming background query, polls
  it at bounded intervals, and calls the native query's `cancel()` when an execution checkpoint
  observes cancellation. The job then transitions to `cancelled` (or `superseded`).
- The completed report contains, per column: dtype, a coarse `kind` classification (Numeric,
  Text, Temporal, Boolean, Nested, Other), null count, NaN count (float dtypes only; `None` for
  every other dtype — not applicable, mirroring `zero_count`/`negative_count`), distinct count
  (`None` when the dtype is unhashable, e.g. `Object`), and — depending on dtype — min/max,
  quartiles/mean/std, zero and negative counts, or a bounded list of the most frequent distinct
  values (top 50). Missingness is a three-way split, not a valid/invalid dichotomy: null (absent),
  NaN (an invalid-numeric value — a float column that is usually numeric but carries a non-numeric
  error/default value materialises NaN, which `null_count` does not see), and everything else
  valid. `distinct_count` counts only those valid values — the null and NaN buckets are excluded,
  so an all-NaN float column reports `distinct_count == 0`, not `1`.
- Every hashable column also reports `unique_ratio` over valid (non-null/non-NaN) rows.
  `is_high_cardinality` is true only for a text-like column whose valid distinct values exceed
  the bounded categorical display limit of 50 — the caution is about display truncation, which
  only text-like columns get; numeric and temporal columns legitimately hold many distinct
  values and are never flagged (ruled 2026-07-27). `is_identifier_candidate` is the narrower, descriptive cue for a
  non-empty, fully populated, all-unique column whose case-insensitive name is `id`, `key`,
  `uuid`, `guid`, starts with `id_`/`key_`, or ends with `_id`/`_key`; it is not a uniqueness
  guarantee for a group of columns. Text-like columns report min/mean/max character length
  after the same lenient display decoding used by their value counts. Temporal columns report
  the formatted max-minus-min span.
- The report also contains a derived data-quality summary: columns with missing (null) values,
  numeric columns with NaN values, constant (single-value) columns, numeric columns with negative
  values, and numeric columns that are ≥95% zero, each rendered as a short, human-readable issue
  with a severity. A missing-values or NaN issue is `"danger"` severity when its worst offending
  column is ≥50% affected, else `"warning"`. A column counts as constant only when every row holds
  the same valid value — one with nulls or NaNs alongside its single value is reported under the
  missing-values/NaN issues instead, not double-counted as constant (ruled 2026-07-16).
- The data-quality summary also carries `duplicate_row_count` and `duplicate_ratio` and adds one
  duplicate-row issue when the count is nonzero. Duplicate rows are counted exactly across the
  full cached row shape in the same aggregation pass; if any column is unhashable (currently an
  Object column), both fields are `None` rather than guessed from a projection. The issue is
  danger severity at 50% duplicates or above and warning otherwise.
- Materialisation always completes even when a column contains data that cannot be strictly cast
  to text (non-UTF-8 `Binary` bytes, `Duration` values): those columns are formatted leniently
  rather than aborting the whole report.
- Downstream graph edits (nodes/edges added after the Explore node) never invalidate the cached
  dataframe or report for that Explore node. Changes to the Explore node's own analysis code,
  its upstream lineage, the pipeline preamble, the source file, or the input source do invalidate
  it. Changes to only the Explore node's `overview`, `pivots`, or `charts` config blocks do **not**
  invalidate the dataframe cache (they are not part of the dataframe cache key), and the equal
  report is served from cache.
- A pivot calculation never executes the graph and never falls back to preview rows. The run and
  member endpoints derive the same Explore dataframe-cache identity as materialisation and return
  the typed `cache_required` outcome when that exact full-data entry is absent.
- Pivot filters are conjunctions of exact member sets. A member is persisted as a typed
  `{kind, value}` scalar so null, NaN, booleans, integers, finite floats, strings, dates,
  datetimes, times, and decimals do not collapse into display text. Empty member sets mean that
  the placed filter is not restricting the field. Float NaN is treated as missing by numeric
  aggregations but remains independently selectable as a filter/group member.
- Rows and Columns retain configured field order. One optional `sort_by` target selects a placed
  Row or Value; without it all Row labels use deterministic ascending order. A selected Row has an
  ascending or descending label order while every other Row level remains ascending; null/NaN
  remain after ordinary values in either direction. A selected Value uses that measure's correctly re-aggregated row
  total across the filtered dataset (not a sum of displayed cells), puts blank aggregate values
  last, and uses ascending Row labels as deterministic tie-breakers. Grand totals
  remain last and Columns paths retain deterministic ascending typed order. Values support `sum`, `count`,
  `average`, `min`, `max`, `median`, and `distinct_count`; numeric-only aggregations reject
  incompatible dtypes, Count counts non-null/non-NaN values, and Distinct count excludes
  null/NaN. Repeated Values are legal because placement ids, not field names, identify measures.
- The v1 result limit is 500 ordinary row groups, 100 ordinary column groups, 50,000 displayed
  cells (including enabled grand-total row/column cells), and 500 filter-member rows. A request
  that exceeds a limit returns the measured dimensions, the limit, and remediation; it is never
  truncated, sampled, downsampled, or partially published.
- A completed result is cached by the exact Explore dataframe-cache key, pivot result schema
  version, ordered calculation placements, exact filters, aggregations, row/value sort settings,
  and total options. Card `name`/`enabled`, Value display names, conditional colour scales, and
  presentation-only/future formatting fields are excluded, so those edits reuse the calculation.
  Starting a newer calculation supersedes only
  an older job for the same source, Explore node, and pivot id.
- `validate_explore_overview` accepts only a dict at the top level with string keys. The five
  known toggle keys must be booleans; any other key's value must be JSON-round-trippable through
  codegen (`None`, `str`, `bool`, `int`, a finite `float`, or nested lists/dicts of the same) so
  a newer UI's overview cards remain readable by, and rewritable through, an older parser/codegen
  pair. An empty `overview` dict is preserved as empty (not defaulted) so callers can choose to
  omit the config entirely rather than emit `overview={}`.
- `validate_explore_charts` accepts only a list of dicts, each a complete version-1 card — a
  versionless item is rejected, never migrated. A v1 chart requires
  supported `version: 1`, unique non-empty id/name, Boolean enabled, `pivot_id` null or non-empty,
  `kind: "combo"`, one Rows category mapping, ordered Value encodings and exact-series overrides,
  complete primary/secondary axes, and a complete legend. Nested mapping ids, Value ids, and exact
  series keys are unique in their scopes; `value_id` belongs only to Value encodings and
  `series_key` only to exact-series overrides, so either identity is rejected in the other shape.
  Marks, axes, colours, stack groups, label/marker flags,
  number formats, legend positions, category rotation, and finite ordered manual bounds are
  strictly typed. Chart-level `orientation` is a required `"vertical"` or `"horizontal"`;
  style-level `stack_normalize` is a required Boolean and requires a non-null `stack_group`;
  the secondary axis carries a required Boolean `enabled`, and a card whose secondary axis is
  disabled while any style is assigned to it is rejected. Like every other known v1 field,
  these are rejected when absent rather than defaulted — writers always persist complete
  cards, so the validators materialise no defaults. A stack group may be used by any mark. Across the union of a card's Value
  encodings and exact-series overrides, every style sharing one `stack_group` must agree on
  `stack_normalize` and on `axis` — a stack never mixes normalisation modes or spans value
  axes. Unknown string-keyed fields survive only under the finite recursively
  simple-literal grammar. An empty list remains empty so callers may omit `charts=[]`.
- `validate_explore_pivots` accepts only a list of dicts, each a complete version-1 card — an
  item without `version` is rejected, never migrated.
  A v1 card requires exactly supported `version: 1`, non-empty `id` and `name`, Boolean
  `enabled`, list-valued `filters`/`columns`/`rows`/`values`, and Boolean
  `options.row_grand_totals`/`options.column_grand_totals`. Card ids and lower-cased trimmed names
  are unique. Placement ids are non-empty and unique across the card; Filter/Rows/Columns reject
  a repeated field within the same zone, while Values may repeat fields. Every known nested field
  is type checked and unknown string-keyed fields are retained only when they use the finite,
  recursively simple-literal grammar. An empty list remains empty so callers may omit
  `pivots=[]`.

## PivotChart invariants

- A chart consumes only one successful guarded `ExplorePivotResult`; it never reads a dataframe,
  executes a graph, aggregates, samples, or owns a backend route/job/cache. Any chart-level Update
  and Cancel action delegates to the selected pivot's existing lifecycle.
- Rows form ordered hierarchical categories. The ordered product of ordinary Column paths and
  pivot Values forms series. Each series key is the canonical versioned JSON identity of the
  Value placement id plus the complete typed Column-member path, so typed members cannot collide.
  Row grand totals are included only when requested. Column grand-total paths are never charted:
  a column grand total is the sum of the other series, so charting it would double-count stacks
  and dwarf clustered columns.
- Every charted pivot Value has one explicit Value encoding, and the adapter rejects a chart
  whose encodings are incomplete. A persisted chart may trail its source pivot's Values (a Value
  added after chart creation); chart consumers reconcile the parsed chart above the adapter by
  seeding one explicit default encoding per unmatched Value, surfaced as a seeded default and
  persisted by the next committed chart edit — never a hard failure and never a silent persisted
  write. Exact series overrides replace that
  Value-level style only for a matching generated series; unmatched overrides remain dormant and
  visible rather than being discarded. Newly observed Column members inherit the explicit Value
  encoding. Null cells are gaps. Boolean, malformed, or non-finite numeric cells fail with
  remediation rather than becoming zero.
- The renderer-neutral adapter limit is 500 categories, 100 series, 20,000 rendered points,
  hierarchy depth 6, and 200 characters per rendered category/series label. Exceeding a limit
  reports its measured dimension and directs the analyst to reduce Pivot Rows, Columns, Values,
  or Filters. No chart is truncated or downsampled.
- Supported marks are `column`, `line`, and `area` on primary or secondary numeric axes; every
  mark may be clustered or assigned to an explicit stack group. Charts render vertically by
  default; `orientation: "horizontal"` swaps the category axis onto the vertical dimension and
  the value axes onto the horizontal one without changing series identities, stacking, or
  bounds semantics. A stack group with `stack_normalize` renders each cell as
  cell ÷ Σ|cells| over the group's non-null cells in that category: results lie in [-1, 1],
  negative shares plot below the axis, null cells stay gaps and are excluded from the
  denominator, and a zero denominator (all cells null or zero) renders gaps for that category
  and appends one adapter warning naming it. Safe number formats are `inherit`, `number`,
  `integer`, `percent`,
  `currency_gbp`, `currency_usd`, and `currency_eur`; persisted configuration cannot inject raw
  renderer options, callbacks, HTML, URLs, or executable formatters. `inherit` renders as the
  General locale format — grouped `en-GB` digits with at most two fraction digits at magnitude
  one or above, at most four significant digits below one, and `0` for zero — applied uniformly
  to axis ticks, data labels, tooltips, and the semantic data table.

## Design rationale

- **Three-way missingness split (valid / null / NaN).** Polars' `null_count` does not see NaN, so
  a float column that is fully populated by `null_count`'s measure could still be entirely
  unusable (all-NaN) without a separate signal. NaN counting is gated strictly on float dtype
  (`_is_float_dtype`, i.e. `Float32`/`Float64`) rather than the broader `is_numeric()`, because
  Polars' `is_nan()` raises `InvalidOperationError` against a non-float numeric column (e.g. an
  integer) — NaN is representationally only possible for float dtypes. `distinct_count` was
  changed to count only valid values (excluding both the null and NaN buckets) so it answers "how
  many distinct real values does this column have" rather than conflating missingness with
  cardinality; this required bumping the report cache-schema version (`EXPLORE_CACHE_VERSION`),
  since an older cached report computed the field differently and would otherwise be served stale.
- **Report/dataframe cache separation.** The dataframe cache (parquet on disk, keyed by
  execution lineage) and the lightweight `ExploreCacheReport` cache (in-process LRU, keyed by
  dataframe cache key + node id + source + report schema version) are deliberately independent.
  This lets an `overview`, `pivots`, or `charts` config change reuse the same materialised dataframe while still
  invalidating only the report if the report schema itself changes (`EXPLORE_CACHE_VERSION`).
- **One batched cancellable streaming collect.** All column stats (min/max, quartiles,
  null/zero/negative counts, bounded categorical value counts, display-label group counts,
  text lengths, temporal spans, and whole-row distinct count) are computed in one Polars
  aggregation. Explore runs it as a native streaming background query so checkpoints can cancel
  the query itself while retaining one-pass cost.
- **Bounded categorical value counts.** Value counts are capped at the top 50 by count
  (`_CATEGORICAL_VALUE_COUNT_LIMIT`) so a high-cardinality column cannot make the report
  unbounded in size. `values_truncated` follows the number of display-label groups emitted by
  that same expression, not raw-value cardinality, and is false when no value counts ran (for
  example, `List` columns).
- **Lenient text formatting for Binary/Duration.** A strict `cast(pl.String)` on `Binary` raises
  on the first non-UTF-8 byte sequence, and Polars cannot `cast` `Duration` to `String` at all;
  either would abort the single batched collect and take down the whole report, not just that
  column. Both are formatted element-wise instead so one problematic column cannot break the
  report for every other column.
- **Round-trippable unknown display keys.** The Explore display validators preserve unrecognised
  keys (rather than stripping them) so that a pipeline `.py` file edited by a newer UI version
  still parses and re-serialises correctly under an older backend, at the cost of restricting
  unknown values to simple literals that are guaranteed to survive a `repr()`/codegen round trip.

## Interactions

- [execution-engine](../execution-engine/high-level.md): supplies `execute_lazy_graph`,
  `ExecutionContext`, `ExecutionProfile.EXPLORE_ANALYSIS`, cancellation, and memory-limit
  admission that the Explore job runs under.
- [io-layer](../io-layer/high-level.md): owns the profiled
  `cancellable_streaming_collect` primitive that lets Explore cancel an in-flight native Polars
  aggregation instead of only changing the background-job status.
- [caching](../caching/high-level.md): supplies `DataFrameExecutionCache`,
  `build_dataframe_execution_cache_request`, `dataframe_graph_input_fingerprint`, and the
  `LRUCache` used for the in-process report cache; `src/haute/_cache.py` supplies the dataframe
  cache invariant.
- [background-jobs](../background-jobs/high-level.md): supplies `JobStore`, `JobLifecycle`, and
  `CancellableJobRegistry`, including the latest-wins cancellation semantics this component uses.
- [server-api](../server-api/high-level.md): supplies shared route helpers (`find_typed_node`,
  `_ensure_source_file`, `_validate_runtime_input_paths`, `flatten_graph`) and mounts the Explore
  router in the application shell.
- [codegen](../codegen/high-level.md) / [expression-parsing](../expression-parsing/high-level.md):
  call the Explore display-config validators when emitting or parsing the `overview=`, `pivots=`,
  and `charts=` kwargs on `@pipeline.explore()`.
- [frontend-preview-explore](../frontend-preview-explore/high-level.md): the consumer of
  `ExploreCacheReport` — renders the current Explore result panes from the fields this component
  computes.

## Failure model

- An Explore node with zero or more than one upstream parent fails synchronously with HTTP 400
  before a job is created; no background work is attempted.
- Posting a missing `node_id` returns HTTP 404; a node that resolves but is not Explore-typed
  returns HTTP 400. Both failures are synchronous (via `find_typed_node`) before a job is created.
- Polling a job id the store has never seen returns HTTP 404.
- Inside the background job, cancellation, execution-admission failures, execution memory-limit
  breaches, `PUBLIC_CONTRACT_ERROR_TYPES` (mapped to `contract_error` with
  `contract_error_job_fields`), and contract/schema mismatches are each caught and mapped to a distinct terminal job
  status (`cancelled`/other cancellation reason, `memory_limited`, `contract_error`) with a
  message payload describing the failure; none of these are retried or silently downgraded.
- Any other exception raised while materialising or summarising is logged
  (`explore_cache_failed`, with traceback) and the job transitions to `error` with `str(exc)` as
  the message — no fallback report is synthesised.
- The Explore display validators raise `ConfigError` (not a generic exception) for invalid
  top-level containers, non-string keys, wrong-typed known fields, or unknown values that are not
  round-trippable. Chart validation also rejects malformed entries and blank or duplicate ids;
  callers do not catch and paper over these failures.

**Statistics-shape caveat.** `ExploreFrameStats`/`_build_frame_stats`
unconditionally include zero/negative counts and quartile fields only for numeric columns,
setting them to `None` otherwise. A column reclassified between numeric and non-numeric across
two runs therefore has a different populated-field set. `nan_count` is narrower still: it is
populated only for float dtypes (`Float32`/`Float64`), so an integer column reclassified to or
from float also flips `nan_count` between `0` and `None`.
