# Explore EDA — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `src/haute/routes/explore.py` | FastAPI router (`/api/explore`): the `/pivots` run/status/cancel/member endpoints. Graph-bearing requests share `flatten_graph`, `_ensure_source_file`, and `_validate_runtime_input_paths` before service delegation. Explore's data is built and reported through the shared node-data routes, so this router owns no materialisation of its own. |
| `src/haute/_frame_profile.py` | The data profile computation itself: per-column statistics and the overview summary (`_build_frame_stats`, `_build_data_quality_summary`, `_build_categorical_summary`, `_build_overview_summary`), independent of any node or cache. |
| `src/haute/routes/_pivot_service.py` | Pivot service: resolves the Explore node's shared data point, leases it for the whole calculation, validates fields/aggregations, applies typed exact-member filters, enforces cardinality before aggregation, runs latest-wins admitted jobs, and caches typed matrices by `(point digest, data version, calculation identity)`. Members answer inside the request through the shared synchronous-analysis helper. |
| `src/haute/_polars_utils.py` | [io-layer](../io-layer/low-level.md)-owned `cancellable_streaming_collect` primitive consumed by Explore so a cancelled analysis interrupts its in-flight native Polars query. |
| `src/haute/_column_summary.py` | Dtype facts shared with the [assistant](../assistant/low-level.md)'s value profiles: `is_unhashable_dtype` (the distinct-count gate), the reserved count-field alias `CATEGORICAL_COUNT_FIELD`, and `json_safe_scalar`. Explore's display formatting stays local to the service; only the facts that a second summariser would otherwise have to rediscover as production failures live here. |
| `src/haute/_explore_overview.py` | Standalone validator for the Explore node's `overview` config dict (`validate_explore_overview`, `EXPLORE_OVERVIEW_TOGGLE_KEYS`). Imported by codegen (`_codegen_builders.py`) and the parser (`_config_builder.py`), not by the service or route module. |
| `src/haute/_explore_chart_contracts.py` | Canonical Pydantic structural authority for version-1 PivotChart cards and their nested category, value, series, style, axes, and legend models. It accepts only finite recursive JSON extension values and owns field shape, required values, literals, and scalar bounds; model validators own Python-side identity, stack, and axis relationships. |
| `src/haute/_explore_charts.py` | Stable persisted-config adapter around the canonical chart models: accepts an ordered list of mappings, returns plain dicts, preserves finite additive fields, and translates Pydantic failures into `ConfigError`. It performs no migration, repair, or default materialisation. |
| `src/haute/_explore_pivots.py` | Standalone strict validator for ordered version-1 Explore pivot cards, placements, typed members, supported aggregations/options, unique ids/names, and simple-literal future fields. Versionless cards are rejected, never migrated. |
| `src/haute/schemas.py` | Shared Explore API contracts owned by [server-api](../server-api/low-level.md): the per-column statistic and overview-summary models the shared profile analysis returns, plus pivot run/status/member requests, typed failures, member/path/value/cell structures, and result matrices. |

## Key types and data structures

- **`PivotDataPoint`** (`_pivot_service.py`, frozen dataclass) — the data one pivot request
  reads: the Explore `node_id`, the `source`, the point identity digest, the resolved
  `data_version`, and the `PointResolution` and `DataPointResolver` the calculation leases it
  with. A pivot materialises nothing itself; it reads the point every consumer of that data
  shares.
- **`ExploreFrameStats`** (`_frame_profile.py`, frozen dataclass) — the intermediate result of
  sequential column batches: `row_count`, `columns: list[ExploreColumnStat]`, and
  `overview_summary: ExploreOverviewSummary`.
- **`ExploreColumnStat`** (`schemas.py`) — per-column stats. `distinct_count` is `None` exactly
  when the dtype is unhashable (`pl.Object`, per `UNHASHABLE_DTYPES`); otherwise it counts only
  valid (non-null, non-NaN) values — `n_unique()` counts the null bucket and the NaN bucket each as
  one distinct value, so column-stat assembly subtracts one for each bucket that is actually
  present before storing it. Numeric-only fields (`p25_value`, `median_value`, `mean_value`,
  `p75_value`, `std_value`, `zero_count`, `negative_count`) default to `None` and are only
  populated when `dtype.is_numeric()`. `nan_count` is narrower still — `None` unless the dtype is
  float (`Float32`/`Float64`, per `_is_float_dtype`), since `is_nan()` raises against a non-float
  numeric column and NaN cannot occur in an integer column at all. `histogram` is `None` for
  non-numeric columns and an `ExploreHistogram` for every numeric one.
- **`ExploreHistogram`** (`schemas.py`) — `status` `ok` (up to `_HISTOGRAM_BIN_COUNT` = 20
  equal-width `ExploreHistogramBin`s `{start, end, count}` from the finite minimum to maximum,
  each half-open except the last, which includes the maximum; the counts sum to `finite_count`),
  `constant` (one bin with `start == end`), `empty` (no finite values, no bins) or `skipped`: past
  `_HISTOGRAM_COLUMN_LIMIT` = 50 numeric columns in schema order (`skipped_reason` `column_limit`,
  both counts `None`), or an integer column with a value beyond `_HISTOGRAM_SAFE_INTEGER`
  (2**53 - 1, the largest integer a browser's JSON parser keeps exactly; `integer_precision`,
  counts reported), whose boundaries would reach the browser rounded together. `finite_count` and `non_finite_count` (NaN plus
  infinities; nulls are neither) come from the profile's first aggregation pass, which also takes
  the extrema in the column's own dtype, so constant detection never compares rounded values.
  `_build_histograms` runs the second pass, one query per `_PROFILE_COLUMN_BATCH_SIZE` binnable
  columns, over `numeric_bin_scale`: a value's bin is the number of interior boundaries at or below
  it, and the reported `start`s are those same boundaries, so every counted value lies inside its
  bin's reported interval. Integer columns (other than 128-bit ones and spans of 2**63 or more)
  get exact integer boundaries, bin k starting at the smallest integer offset at or above
  `span * k / bins`, compared as exact offsets from the minimum and reported as integers so large
  identifiers survive the response; a range narrower than 20 gets one bin per value. Float and
  Decimal columns use `min + (max - min) * k / 20` and compare their `Float64` values.
- **`ExploreOverviewSummary`** (`schemas.py`) — `data_quality: ExploreDataQualitySummary` plus
  `categorical_summary: list[ExploreCategoricalColumnProfile]`, one profile per non-numeric
  column that has a schema stat.
- **`NodeDataProfile`** (`schemas.py`, owned by [server-api](../server-api/low-level.md)) — what
  Explore renders instead of a report of its own: `row_count`, `column_count`, `columns`,
  `overview_summary`, the `data_version` the statistics were computed from, and `generated_at`.
  It carries no Explore node identity, because every consumer of the point shares it.
- **`EXPLORE_OVERVIEW_TOGGLE_KEYS`** (`_explore_overview.py`) — frozenset of the five known
  overview card keys: `dataset_snapshot`, `data_quality`, `numeric_summary`,
  `categorical_summary`, `schema`. These must map to `bool`; any other key must map to a
  "round-trippable" value per `_is_round_trippable_overview_value` (recursively: `None`, `str`,
  `bool`, `int`, finite `float`, or `list`/`dict` of the same, with dict keys required to be
  `str`).
- **`ExploreChartConfig`** (`_explore_chart_contracts.py`, Pydantic model) — the
  canonical structural authority for one persisted version-1 ComboChart with stable
  id/name/enabled/source-pivot linkage, chart `orientation` (`"vertical"`/`"horizontal"`), Rows
  category settings, ordered Value encodings and exact
  series overrides, primary/secondary axes, and legend. Style mappings contain only closed mark,
  axis, number-format, colour, stack (`stack_group` plus Boolean `stack_normalize`), marker, and
  label values. Unknown string-keyed fields are retained only when their values
  satisfy the finite recursive JSON grammar.
- **`ExplorePivotPersistedConfig` / `ExplorePivotConfig`** (`src/haute/_types.py`, owned by
  [server-api](../server-api/low-level.md)) — the version-1 persisted card contains `id`, `name`,
  `enabled`, ordered Filter/Columns/Rows/Values placements, ordered
  selected shared-formula ids, a combined `value_order` of Value/formula ids, and grand-total
  options. The runtime form replaces those ids with resolved formula objects for calculation
  requests. `value_order` is required and must cover the exact Value/formula id union once.
  **`ExplorePivotFormula`** definitions live
  once in the Explore-level `pivot_formulas` library. Each
  placement owns a stable id. Filter members are typed scalars and Value placements add one of
  the seven supported aggregations, a stable field-first aggregation reference, and a
  presentation-only display name. A formula owns a stable id/reference, display name, a Python
  expression returning one Polars `pl.Expr`, and numeric presentation settings. Persisted Value
  and formula references are required, each pivot's `formulas` list contains ids only, and shared
  definitions exist only in `pivot_formulas`; missing fields and inline definitions fail
  validation without migration. The frontend runtime parser resolves each selected id to its
  shared formula object before sending a calculation request to `PivotService`.
  Column, Row, Value, and formula
  placements also own presentation-only `number_format`, `decimal_places: None | 0..10`, and
  `use_grouping` settings. The closed formats are General, Number, Percentage, and GBP/USD/EUR
  currency. Every numeric-format field is required on a version-1 card. Value placements
  additionally own `color_scale` and required nullable `color_scale_split_by`; a non-null split is
  valid only for an active scale and references a current Row/Column placement id. Missing fields
  are rejected rather than defaulted or migrated.
- **`PivotCalculationSpec`** (`_pivot_service.py`, frozen dataclass) — resolved Explore cache
  request/key, validated v1 pivot, calculation hash, result-cache key, and latest-wins family key
  `("explore_pivot", source_file, node_id, source, pivot_id)`.
- **`ExplorePivotResult`** (`schemas.py`) — result schema version, Explore/pivot/source/cache and
  calculation identities, ordered row/column field names, stable value identities, typed row and
  column paths with total markers, typed cells, warnings, generation time, and execution metrics.
  Result values never carry card/value display names; the UI joins those from current persisted
  presentation config so rename-only reuse cannot surface stale labels.
- **`PROFILE_ANALYSIS_VERSION`** (`routes/_node_data_service.py`) — the analysis version of the
  profile, part of its key in the analysis-result store, so an incompatible statistics payload is
  recomputed rather than served. The current version includes NaN counts, valid-values-only
  distinct counts, categorical truncation based on display-label groups, column quality profiles,
  and exact duplicate-row statistics.

## Pivot calculation invariants

- `EXPLORE_PIVOT_RESULT_VERSION = 1`; limits are `MAX_ROW_GROUPS = 500`,
  `MAX_COLUMN_GROUPS = 100`, `MAX_DISPLAY_CELLS = 50_000`, and
  `MAX_FILTER_MEMBERS = 500`. Cardinalities are collected first under the admitted context. The
  displayed-cell check includes each requested total path and uses the selected output count,
  `max(len(values) + len(selected_formulas), 1)`, so even an unconfigured layout has a bounded
  shape.
- The shared [point resolver](../caching/low-level.md#data-points) is the sole derivation of the
  data a pivot reads. `PivotService` resolves the Explore node's point, refuses to calculate
  unless it resolves `current` with a data version (a typed local `cache_required` failure, never
  fresh execution), and holds `lease_resolved(..., exact=True)` for the whole calculation, so
  every collect reads the generation the request resolved and a concurrent clear cannot remove it
  underneath. It never invokes graph execution.
- **One result, one data version.** A result is labelled and cached with the data version the
  request resolved, so it must be computed from exactly that data. The lease is therefore exact —
  data that moved on between resolution and lease raises rather than being read under the old
  version — and a point read straight from its file, which no lease pins, is re-resolved after
  the aggregation to prove it was not rewritten while it was read. Either outcome, and a
  generation retired underneath the calculation, ends the job `contract_error` carrying the
  `cache_required` failure: the client asks again for whatever the point holds now instead of
  receiving a matrix attributed to data that is gone. `members` answers the same way, and its
  memo keeps nothing it could not attribute to a version.
- Calculation canonicalisation includes only ordered filter field/member keys, ordered row and
  column fields plus their effective Row directions, Value placement ids/fields/aggregations/
  references plus the selected Value direction, ordered selected shared-formula
  ids/references/expressions, the combined `value_order`, total
  options, and result schema version; the point digest and data version key the cached matrix
  alongside that identity rather than forming part of it. A
  `sort_by` selection whose effective ordering is unchanged reuses the same key. It excludes
  card name/enabled, Value/formula display names, all Column/Row/Value/formula numeric-format
  settings, Value colour scales and their split-by references, and all unknown presentation
  fields. The in-process result LRU stores at most 32 matrices.
- Physical aggregation aliases are internal. Public Value references use field-first names such as
  `total_claims_sum` and `total_claims_mean`. Exact `(field, aggregation)` duplicates are computed
  once per grouping query and projected to each stable Value reference; different aggregations of
  the same field remain separate expressions in the same Polars aggregation. Formula expressions
  are compiled once per pivot calculation with the full `pl` namespace under the existing
  user-code AST sandbox and added to that same grouped query. They use source fields directly and
  declare their own aggregations, for example `pl.col("total_claims").sum() * 2`; neither visible
  Value references nor earlier formula references are inputs. Polars expression metadata supplies
  root source-column names, which must exist in the Explore dataframe schema. Planning against a
  grouped schema requires each expression to produce one supported scalar rather than a List,
  Struct, Object, or other nested output. No selected Value or hidden dependency is required.
  Each expression is physically aliased to a reserved name derived from its stable placement id,
  so a public formula reference may safely equal a grouped source-field name. Formula and ordinary
  Value outputs follow the pivot's combined `value_order` and use the existing value-cell
  coordinates; their result
  identity uses the formula reference as `field` and `aggregation="formula"`. While a formula
  editor is open, the existing source-field selector replaces its placement buttons with one
  `Add to: Formula` action that inserts `pl.col(<source field>)` at the cursor; it does not infer
  compatibility from one pivot's Values. The shared-formula library uses a dedicated search input
  followed by a content-sized list of compact formula rows that becomes vertically scrolling at
  its maximum height; its single active add/edit form remains outside that list.
  Selected formula cards use the same pointer and Up/Down keyboard reorder affordances as ordinary
  Values, but their drag targets are restricted to Values; Filters, Columns, Rows, and Left-arrow
  movement out of Values remain blocked.
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

- `frontend/src/panels/explore/chartConfig.ts` consumes generated chart types/constants and a
  separate lazy standalone validator derived from the canonical Pydantic schema. Generated code
  owns the version-1 structural fields, literals, required values, and bounds; the handwritten
  adapter retains non-plain/finite-JSON rejection, stable errors, canonical series identities,
  duplicates, stack and axis relationships (including required orientation and
  stack-normalisation flags, any-mark stacking, card-wide
  stack-group consistency, and the secondary axis's required Boolean `enabled` — rejected
  when absent or when disabled while any style
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

### Explore's data

Explore materialises nothing of its own. Its node resolves to a shared data point through the
[caching](../caching/low-level.md#data-points) resolver, and that point is reported, built,
cleared and profiled through the
[node-data routes](../server-api/low-level.md#node-data-builds):

1. A blank-code Explore node reads the point of its single upstream input, so an Explore node
   wired straight to a Data Input analyses that file — with no node-output build at all — and an
   `apiInput` port is analysed from its table cache. An Explore node with code reads its own
   node output, which has to be built.
2. Every consumer of that point shares its build, its state, and its statistics: the `profile`
   analysis of the point's current data version, stored by data version in the
   [analysis-result store](../caching/low-level.md#analysis-results). A Banding or Rating editor
   on the same input therefore sees the same cache state and the same running build.
3. `_build_frame_stats` below is that profile computation. It is run by the profile job, never by
   an Explore-owned worker, and its result carries no Explore node identity.

### `_build_frame_stats` — sequential column profiling

Given `lf: pl.LazyFrame` and `schema: pl.Schema`:

1. Builds aggregation batches of at most eight source columns, with
   `pl.len().alias("row_count")` in the first batch, then per column `name`/`dtype`:
   - `null_count().alias(f"null::{name}")` always.
   - `n_unique().alias(f"unique::{name}")` unless `is_unhashable_dtype(dtype)`.
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
   failure. Batches run sequentially against the materialised Parquet frame, letting projection
   limit each scan and avoiding simultaneous distinct/quantile state for every column.
   Results remain exact; this is not sampling. Duplicate rows are counted separately when
   every dtype is hashable: a raw column distinct count equal to the row count proves zero
   duplicates; a one-column frame reuses its distinct count; otherwise an isolated
   `pl.struct(all columns).n_unique()` query provides the exact count. It never overlaps
   the column-statistic queries. Unsupported duplicate profiling remains unknown.
3. Iterates columns again to build `ExploreColumnStat` entries from the merged aggregate row,
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
   `row_count - unique_rows` when every column is hashable, using the unique-column
   proof or one-column reuse where possible; otherwise it remains `None`.
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
3. **Constant/single-value columns** — `row_count > 0 and distinct_count == 1 and null_count == 0 and
   not (nan_count or 0)`, excluding columns already flagged zero-heavy (see below) so a column is not
   double-counted between the two issues. Constant means every row holds the same valid value, with
   zero tolerance for null or NaN rows — a column that is single-valued except for missing or NaN rows
   is reported under issue 1/2 instead (ruled 2026-07-16).
4. **Numeric columns with negatives** — any column with `negative_count > 0`, sorted by
   descending count; detail names only the top offender with its row count.
5. **Mostly-zero numeric columns** — `(row_count - null_count) > 0 and zero_count > 0 and
   zero_count / (row_count - null_count) >= 0.95`, sorted by descending zero count; this issue is
   computed and referenced (via `zero_heavy_names`) before the constant-column issue is appended,
   even though it is appended after the constant and negative issues.
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

- **Object dtype**: excluded from `n_unique` entirely (`is_unhashable_dtype`); `distinct_count`
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
  `_CATEGORICAL_VALUE_FIELD`/`CATEGORICAL_COUNT_FIELD` prefixed with `__haute_`) avoids
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
- **Downstream-edit cache stability**: the data point an Explore node reads, and therefore its
  data version, is unchanged by edits to nodes downstream of it and by adding an `overview`,
  `pivots`, or `charts` block to its own config — the display payload is not part of the node
  snapshot signature. It *is* changed by editing the Explore node's own analysis `code`, which
  changes the node output it reads.

## Error handling

- `HTTPException(status_code=400, ...)` — raised synchronously (not inside the background job)
  by the shared point resolver when the target node has zero or multiple upstream parents, and by
  `find_typed_node` when a pivot's node is not Explore-typed. A missing node id returns HTTP 404.
  These surface directly as the HTTP response; no job is created.
- `ConfigError` (from `haute.errors`) — raised by the Explore display-config validators for
  structurally invalid overview/pivot/chart values, including malformed cards and duplicate ids.
  Raised during codegen/parse, not during the run/status/cancel routes.
- Build and profile failures — cancellation, admission, memory limits, and contract and schema
  mismatches — are terminal job states of the shared
  [node-data jobs](../server-api/low-level.md#node-data-builds), not of an Explore-owned worker.
  Explore renders whatever state the point reports; no failure of the data propagates out of the
  background thread, and none aborts the process.
- Pivot failures stay Explore's own: a pivot whose point has no data asks for a build instead of
  aggregating, and typed filter, dtype, and cardinality failures answer as the pivot job's typed
  failure payload (see Control flow).
- `JobStore.require_job` (used by the pivot `status`/`cancel` routes) raises
  `HTTPException(404, "Job '{job_id}' not found")` directly on an unknown job id; this behaviour
  is inherited from the shared [background-jobs](../background-jobs/high-level.md) `JobStore`,
  not implemented in this component.

## Testing

- `tests/test_frame_profile.py` — direct unit tests of `_build_frame_stats` (no HTTP layer),
  the computation the shared profile analysis runs, including exact histogram bins for nullable,
  constant, negative, NaN/infinite, all-null, integer and Decimal columns, the wide-schema column
  limit, and non-numeric columns having none,
  covering: Object vs. Struct distinct-count handling, empty schema, numeric
  profile fields, boolean min/max-vs-value-count casing consistency, all-null numeric columns,
  the full data-quality summary issue set and ordering, bounded categorical value counts
  (including exactly-50 and >50 truncation boundaries), unsupported (nested/list) categorical
  profiles, the `count`-named-column aliasing collision guard, Binary/Duration lenient
  formatting, and sequential bounded-width batches (call-count assertion plus query-plan
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
- `tests/test_analysis_results.py` — the profile through `POST /api/node-data/profile`: an
  Explore node's point profiled once and then served from the analysis store, a rebuilt point
  never answering with the previous data version's profile, one column-statistics run against a
  Data Input read directly (an Explore-equivalent consumer wired straight to a source, so no
  node output is built at all), and the failures that store nothing: an unadmitted profile and
  one over its memory budget (`memory_limited`), a cancelled profile, a profile whose direct file
  is rewritten underneath it (`contract_error` with `node_data_changed`), and a clear racing a
  profile that is publishing.
- `tests/test_node_data_routes.py` — the resolve, build, refresh and clear lifecycle Explore now
  shares with every other consumer: zero and multiple upstream parents, missing nodes,
  named-source isolation, and the data-version invalidation matrix for upstream code, the
  consumer's own code, the preamble, the source file, and the input source.
- `tests/test_explore_round_trip.py` — exercises the Explore display validators indirectly
  through `graph_to_code` → `parse_pipeline_source`: overview toggle/unknown-key round trips,
  ordered pivot and enabled/disabled chart cards with future settings, and omission of empty
  `overview={}`/`pivots=[]`/`charts=[]` kwargs.
- `tests/test_explore_charts.py` — pins chart-container/card shape validation, required state,
  duplicate-id rejection, future simple-literal fields, order preservation, and detached output.
- `tests/test_explore_pivots.py` — pins pivot-container/card shape validation, duplicate-id
  rejection, future simple-literal fields, order preservation, and detached output.
- `tests/test_explore_pivot_routes.py` exercises the point a pivot reads, typed filters,
  bounded aggregation/cardinality, sorting/totals, latest-wins job lifecycle, member lookup,
  export, and JSON-safe scalar results including Binary and Duration min/max values.
  Its runtime type matrix covers every persisted member kind (null, NaN, string, Boolean,
  integer, finite float, decimal, date, datetime, and time), dtype-mismatch failures, and
  unsupported dimension/value dtypes rather than stopping at config validation. The four hard
  bounds (row groups, column groups, displayed cells, and filter members) are each pinned at and
  immediately above their limit.
  It also pins the data the calculation reads: a pivot on an unbuilt point asks for the build
  rather than computing on stale data, a rebuild between two pivot runs is never served the
  previous data version's matrix, a pivot holds the point leased for its whole calculation so a
  concurrent clear cannot remove the data underneath it, a dropped result cache recalculates from
  the published snapshot without executing the graph, and a file rewritten either before or
  during the calculation ends the job `cache_required` instead of publishing a matrix labelled
  with the version that is gone — the same for a members lookup.
## Exact profile duplicate counting scratch

Whole-row distinct counting uses at most 250,000 estimated rows per hash
partition, reduced by decoded row width when the execution budget requires it.
Small inputs retain the direct scalar aggregation. Large inputs are written
once with Polars' partitioned Parquet sink, then each partition is counted
exactly and the scalar counts are summed. Hashes only route rows; collisions
cannot change the answer. Temporary routing columns never enter the files.
Column quantiles/distinct counts retain their existing sequential batches and
exact semantics. Skew and sampling error remain subject to the existing worker
memory and timeout limits; the partition size is not a hard memory guarantee.

The parent profile job owns the scratch directory and removes it only after
the isolated worker has exited, including cancellation, failure and forced
termination. Large direct calls must supply a scratch owner. Disk headroom is
checked before the partition write, and written bytes enter execution metrics.
