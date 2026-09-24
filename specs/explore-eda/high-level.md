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

- Computing the statistics an Explore node renders — row count, column count, per-column schema
  stats (`ExploreColumnStat`), a data-quality summary, per-column bounded categorical value
  counts, text/temporal cues, cardinality flags, capped server-binned histograms of numeric
  columns, and an exact duplicate-row count when every column is hashable — as the `profile`
  analysis of a shared data point, so every consumer of that data sees the same statistics.
- Relating chosen features to a numeric target (optionally weighted) and checking whether chosen
  key columns identify a row, over the whole data point, on demand: bounded levels per feature,
  ranked by the share of the target's variance they explain, and exact key counts.
- Validating the `overview` config dict attached to an Explore node — the set of toggle cards
  (`dataset_snapshot`, `data_quality`, `numeric_summary`, `categorical_summary`, `schema`) a user
  has enabled, plus round-trip-safe storage of any unrecognised keys.
- Validating the ordered, versioned `charts` config list attached to an Explore node. Every
  ComboChart card is complete version 1 — there is no legacy migration, and a card without
  `version: 1` is rejected. Every card has a unique id and
  case-insensitively unique name, a nullable stable pivot id, typed category/Value/series
  mappings, two typed numeric axes, and a typed legend; a field the chart models do not declare
  is rejected by name at every nesting level.
- Validating the ordered, versioned `pivots` config list attached to an Explore node. Every card
  is complete version 1 — there is no legacy migration, and a card without `version: 1` is
  rejected. Every card has a unique id,
  case-insensitively unique name, visibility state, typed Filter/Columns/Rows/Values placements,
  and row/column grand-total options; a field the validator does not know is rejected by name.
- Starting, polling, and cancelling pivot calculations over the Explore node's leased data point
  (`POST /api/explore/pivots/run`, `GET /api/explore/pivots/status/{job_id}`,
  `POST /api/explore/pivots/cancel/{job_id}`), and listing exact filter members from that same
  point (`POST /api/explore/pivots/members`).
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
- Resolving a consumer node to a data point, building or joining that point's snapshot,
  reporting and clearing it, and storing an analysis of it by data version — see
  [caching](../caching/high-level.md) and the node-data routes in
  [server-api](../server-api/high-level.md). Explore starts, polls, and cancels its data through
  those shared routes and owns no materialisation of its own.
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

- An Explore node reads a shared data point: its single upstream input when its own analysis
  code is blank, its own node output otherwise. The node in that graph must have exactly one
  upstream parent; otherwise the request fails immediately with an HTTP 400 before any execution
  starts. An Explore node wired straight to a Data Input or an API-input table analyses that
  source directly, with no node output built at all.
- The point's state, its build, its refresh and its clearing are the shared
  [node-data routes](../server-api/high-level.md), not Explore's own. Every other consumer of the
  same data — a Banding or Rating editor on the same parent — shares that one build, sees the same
  state, and joins the same running job instead of starting a second one.
- Explore's statistics are the `profile` analysis of the point's current data version, computed
  once in a killable worker process under the admitted `EXPLORE_ANALYSIS` memory limit and then
  stored by data version. Reopening the node, or opening it in another pane, serves the stored
  profile without recomputing it; a rebuilt point is never answered with the previous data
  version's profile. A profile that is cancelled, not admitted, over its memory budget, or
  computed from data that changed underneath it stores nothing.
- A group-by in the lineage feeding the point is admitted as part of that explicit full-frame
  build only when its source-derived peak-memory estimate fits the admitted `EXPLORE_ANALYSIS`
  headroom. Missing estimates or insufficient headroom fail with the existing typed
  execution-strategy error; Explore never substitutes a partial aggregation or a generic chunked
  execution.
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
- Downstream graph edits (nodes/edges added after the Explore node) never change the point an
  Explore node reads, so they never invalidate its data or its profile. Changes to the Explore
  node's own analysis code, its upstream lineage, the pipeline preamble, the source file, or the
  input source do change it. Changes to only the Explore node's `overview`, `pivots`, or `charts`
  config blocks do **not** (the display payload is not part of the snapshot signature), and the
  stored profile is served unchanged.
- A pivot calculation never executes the graph and never falls back to preview rows. The run and
  member endpoints resolve the same point and return the typed `cache_required` outcome with its
  state when that point has no current data. A pivot leases the point for its whole calculation,
  so a concurrent clear or rebuild cannot change the data underneath a running aggregation, and a
  result computed for one data version is never returned for another.
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
  Each Value also owns a stable, unique formula reference derived from its field followed by its
  aggregation (for example `total_claims_sum` and `total_claims_mean`). Repeating the exact same
  field/aggregation appends a numeric suffix.
- An Explore node owns one ordered library of shared calculated fields in `pivot_formulas`; a
  formula is defined once and can be added to the Values area of any compatible pivot. Each pivot
  persists its selected formula ids plus one `value_order` containing every ordinary Value and
  selected formula id exactly once. The Values area renders that combined order: ordinary Values
  retain their existing cross-area drag behaviour, while formulas can be dragged or keyboard-
  reordered only within Values and are blocked from Filters, Columns, and Rows. This order is the
  displayed pivot-column order. `value_order` is required on every version-1 card; an incomplete
  card is rejected rather than migrated. Beneath the normal field selector, the formula
  area mirrors the source-field picker with a formula search input and a content-sized list that
  grows to a bounded, scrolling maximum. Its compact calculated-field rows expose `Edit formula`
  and `Add to: Values` actions. It opens one
  add/edit form at a time beneath the list and does not leave every code editor expanded. A
  selected formula is also visible and removable in that pivot's Values area.
- Each shared formula has a stable id/reference, display name, one Python expression that must
  return a Polars `pl.Expr`, and shared number-formatting settings. The expression receives the
  complete Polars namespace and the existing user-code sandbox rules; there is no formula-function
  allowlist. A formula is a self-contained grouped expression over source fields, for example
  `pl.col("total_claims").sum() * 2`; source fields do not have to be present in the pivot's visible
  Values area. While a formula editor is open, each source-field row temporarily replaces its
  normal Filter/Columns/Rows/Values placement actions with `Add to: Formula`, which inserts that
  field's `pl.col(...)` expression at the editor cursor; closing the editor restores the normal
  placement actions. `Add to: Values` depends only on whether that formula is already selected and
  whether its output reference would collide. Value/formula output aliases are identities, not
  formula inputs, so formulas do not depend on selected Values or earlier formulas and there is no
  missing-Value dependency state.
  The engine derives root source-column names from Polars, rejects unavailable source fields, and
  requires the expression to produce one scalar aggregate per group; an unaggregated column
  expression that produces a list is invalid. It never inserts a hidden Value, guesses an
  aggregation, or drops the formula implicitly.
  The engine evaluates selected formula expressions alongside Values for each ordinary or total
  grouping context, before the grouped result is widened into the displayed matrix.
  Consequently one definition applies to every Rows/Columns bucket and every pivot that selects
  it, while totals aggregate the underlying filtered rows first and then evaluate the formula
  rather than combining displayed formula cells. A syntax error, unavailable source field,
  non-`pl.Expr` result, non-scalar grouped result, or incompatible Polars expression fails clearly
  as an invalid pivot formula; it is never omitted or replaced with a fallback value.
- Each Value may persist a presentation-only conditional colour scale and an optional split-by
  placement id. A split is valid only for an active scale and must reference a currently placed Row
  or Column; omitted split fields normalise to null. It does not change aggregation or result order.
- The v1 result limit is 500 ordinary row groups, 100 ordinary column groups, 50,000 displayed
  cells (including enabled grand-total row/column cells), and 500 filter-member rows. A request
  that exceeds a limit returns the measured dimensions, the limit, and remediation; it is never
  truncated, sampled, downsampled, or partially published.
- A completed result is cached by the exact point digest and data version, pivot result schema
  version, ordered calculation placements, exact filters, aggregations, row/value sort settings,
  ordered selected formula ids/references/expressions, the combined `value_order`, and total
  options. Card `name`/`enabled`,
  Value or formula display names, number-format, decimal-place and grouping settings, conditional
  colour scales and their split-by references, and
  presentation-only/future formatting fields
  are excluded, so those edits reuse the calculation.
  Starting a newer calculation supersedes only
  an older job for the same source, Explore node, and pivot id.
- `validate_explore_overview` accepts only a dict at the top level whose keys are the five
  known card keys, each a boolean. Any other key is rejected with a `ConfigError` naming it and
  the known cards: the [canonical-input rule](../README.md#canonical-only-format-policy) allows
  no forward-compatibility passthrough. An empty `overview` dict is preserved as empty (not
  defaulted) so callers can choose to omit the config entirely rather than emit `overview={}`.
- Canonical Pydantic chart models structurally validate each complete version-1 card;
  `validate_explore_charts` is the stable persisted-config adapter that accepts only a list of
  dicts, returns plain dicts, and maps validation failures to `ConfigError`. The same model schema
  generates the browser's static chart types and standalone structural validator. Versionless
  items are rejected, never migrated, and neither boundary repairs or materialises defaults.
  Explicit Python and browser semantic layers retain identity, duplicate, stack, axis, and stable
  error-policy checks that are not represented as a second structural schema. A v1 chart requires
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
  `options.row_grand_totals`/`options.column_grand_totals`; `formulas` is a required list of unique
  ids from the Explore node's `pivot_formulas` library. `value_order` is a required list of
  non-empty unique ids that covers every placed Value and selected formula exactly once.
  Card ids and lower-cased trimmed names are unique. Placement ids are non-empty and unique across
  the card; Filter/Rows/Columns reject a repeated field within the same zone, while Values may
  repeat fields. Value references are required valid non-reserved identifiers and unique within
  the card. Shared formula definitions live only in `pivot_formulas`; formula references are
  required, and shared formula ids/references are unique in the Explore node. Inline formula
  objects, missing references, missing formula selections, and incomplete numeric-format or
  conditional-format fields are rejected rather than migrated.
  Every known nested field
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
  cardinality; this required bumping the profile's analysis version
  (`PROFILE_ANALYSIS_VERSION`), since a stored profile computed the field differently would
  otherwise be served stale.
- **Bounded server-binned distributions.** Each numeric column's histogram is computed on the
  server from the profile's own streaming passes, never from rows sent to the browser: the first
  pass adds the finite minimum, maximum, finite count and non-finite count, and a second batched
  pass counts up to 20 equal-width bins only for columns that have a range; integer columns get
  exact integer boundaries, so large identifiers are neither merged nor misreported. Null, NaN and infinite
  values never enter a bin. A wide schema bins only its first 50 numeric columns and reports the
  rest as skipped, so the extra aggregation state is bounded; constant and all-missing columns
  say so explicitly instead of drawing an empty chart. Adding the histograms bumped
  `PROFILE_ANALYSIS_VERSION` to 2, so stored profiles are recomputed rather than served
  without them.
- **Relationships answer inside the request.** Relationships and key checks use the same
  synchronous-analysis surface as banding statistics and rating levels rather than a background
  job: the pane asks while it is open, a disconnect cancels the scan, a changed question aborts
  the previous one, and answers are memoised by data version and question. The choices are the
  pane's own state, not node config, so asking never edits the pipeline.
- **Data and analysis kept apart.** The point's snapshot (parquet on disk, keyed by the node
  snapshot signature) and the profile stored against it (keyed by point digest, data version,
  analysis kind, and analysis version) are deliberately independent. This lets an `overview`,
  `pivots`, or `charts` config change reuse the same data and the same profile, while an
  incompatible statistics payload is recomputed without rebuilding the data.
- **Sequential cancellable profiling batches.** Exact column stats (min/max, quartiles,
  null/zero/negative counts, bounded categorical value counts, display-label group counts,
  text lengths, and temporal spans) are computed in batches of at most eight columns
  against the point's leased frame. This avoids retaining all columns' aggregation state
  simultaneously. Each batch remains a cancellable native streaming query. Whole-row
  distinct counting runs separately when needed; an exactly unique column proves zero
  duplicate rows, and a one-column frame reuses its column distinct count.
- **Bounded categorical value counts.** Value counts are capped at the top 50 by count
  (`_CATEGORICAL_VALUE_COUNT_LIMIT`) so a high-cardinality column cannot make the report
  unbounded in size. `values_truncated` follows the number of display-label groups emitted by
  that same expression, not raw-value cardinality, and is false when no value counts ran (for
  example, `List` columns).
- **Lenient text formatting for Binary/Duration.** A strict `cast(pl.String)` on `Binary` raises
  on the first non-UTF-8 byte sequence, and Polars cannot `cast` `Duration` to `String` at all;
  either would abort profiling and take down the whole report, not just that
  column. Both are formatted element-wise instead so one problematic column cannot break the
  report for every other column.
- **Unknown display keys.** The overview, pivot and chart validators reject a key they do not
  know, naming it, under the canonical-input rule: there is no forward-compatibility
  passthrough, so a config written by a newer editor fails loudly instead of being carried
  unread.

## Interactions

- [execution-engine](../execution-engine/high-level.md): supplies `execute_lazy_graph`,
  `ExecutionContext`, `ExecutionProfile.EXPLORE_ANALYSIS`, cancellation, and memory-limit
  admission that the Explore job runs under.
- [io-layer](../io-layer/high-level.md): owns the profiled
  `cancellable_streaming_collect` primitive that lets Explore cancel an in-flight native Polars
  aggregation instead of only changing the background-job status.
- [caching](../caching/high-level.md): resolves an Explore node to its data point, leases the
  point's frame for profiling and for every pivot calculation, and stores the profile by data
  version in the analysis-result store.
- [background-jobs](../background-jobs/high-level.md): supplies `JobStore`, `JobLifecycle`, and
  `CancellableJobRegistry`, including the latest-wins cancellation semantics this component uses.
- [server-api](../server-api/high-level.md): owns the node-data routes Explore's data is built,
  reported, cleared, and profiled through; supplies shared route helpers (`find_typed_node`,
  `_ensure_source_file`, `_validate_runtime_input_paths`, `flatten_graph`); and mounts the Explore
  pivot router in the application shell.
- [codegen](../codegen/high-level.md) / [expression-parsing](../expression-parsing/high-level.md):
  call the Explore display-config validators when emitting or parsing the `overview=`, `pivots=`,
  and `charts=` kwargs on `@pipeline.explore()`.
- [frontend-preview-explore](../frontend-preview-explore/high-level.md): the consumer of
  `NodeDataProfile` — renders the current Explore result panes from the fields this component
  computes.

## Failure model

- An Explore node with zero or more than one upstream parent fails synchronously with HTTP 400
  before a job is created; no background work is attempted.
- Posting a missing `node_id` returns HTTP 404; a node that resolves but is not Explore-typed
  returns HTTP 400. Both failures are synchronous (via `find_typed_node`) before a job is created.
- Polling a job id the store has never seen returns HTTP 404.
- Failures of the data and of the profile are terminal states of the shared node-data jobs —
  cancellation, admission failures, memory-limit breaches, contract and schema mismatches, and
  internal errors each map to their own status with a message payload, and none is retried or
  silently downgraded. Explore renders the state the point reports; it synthesises no fallback
  statistics and never treats a failed profile as an empty one.
- A pivot or member request against a point with no current data answers `cache_required` with
  that state, never a partial aggregation.
- The Explore display validators raise `ConfigError` (not a generic exception) for invalid
  top-level containers, non-string keys, wrong-typed known fields, an unknown overview key, or
  unknown pivot/chart values that are not round-trippable. Chart validation also rejects malformed entries and blank or duplicate ids;
  callers do not catch and paper over these failures.

**Statistics-shape caveat.** `NodeDataProfile`/`_build_frame_stats`
unconditionally include zero/negative counts and quartile fields only for numeric columns,
setting them to `None` otherwise. A column reclassified between numeric and non-numeric across
two runs therefore has a different populated-field set. `nan_count` is narrower still: it is
populated only for float dtypes (`Float32`/`Float64`), so an integer column reclassified to or
from float also flips `nan_count` between `0` and `None`.
