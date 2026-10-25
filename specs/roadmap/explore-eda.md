# Explore / EDA roadmap

## Scope

Explore reports and their attached pivot/chart presentations provide correct,
bounded, cached analysis and clear analyst workflows. Current behaviour is
specified in [Explore / EDA](../explore-eda/high-level.md),
[frontend node editors](../frontend-node-editors/high-level.md), and
[frontend preview/explore](../frontend-preview-explore/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| EDA-E18 | Deferred | P3 | Evaluate advanced Excel-parity pivot operations after representative use. |
| EDA-E23 | Deferred | P3 | Add shared chart filter and hierarchy interactions after representative use. |
| EDA-E24 | Deferred | P3 | Evaluate the remaining PivotChart parity surface from evidence. |
| EDA-R01 | Planned | P2 | A pivot request is answered right: an invalid card is a 400, a filter on a time-zone-aware column matches, a formula failure names its cause, and a relationships question scans each feature once. |
| EDA-R02 | Planned | P3 | Card fields are required or documented as defaulted, the specification's stale statements are corrected, and the duplicate-count scratch never partitions down to single rows. |
| EDA-R03 | Planned | P3 | An empty pivot result charts as empty rather than as a configuration error, the chart environment observer exists once, nulls use the glyph the specification names, a pivot start failure is recorded without a fabricated job id, and the Explore panels' dead exports are gone. |

`EDA-R01` to `EDA-R03` come from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Planned improvements

### EDA-R01 — Pivot requests answered right

**Why:** An invalid pivot card posted to the run route is answered with the
sanitized 500: `PivotService.start` validates it with `validate_explore_pivots`,
which raises `ConfigError`, and neither the route nor the application handlers
claim that type while the request model types the card as a plain dict. A
filter member on a time-zone-aware `Datetime` column can never match: the
members endpoint emits the value with its offset, `_member_literal` restores an
aware `datetime`, `pl.lit` types it UTC, and the comparison against a
`Europe/London` column raises `SchemaError` (probed), so the job ends `error`
with Polars' message. Every formula failure (a syntax error, a sandbox refusal,
a non-expression result) is reported as "Pivot formula is invalid." with the
cause chained but never shown or logged. Each categorical feature of a
relationships question collects the same grouped plan three times and each
numeric feature twice, so one question of fifty features scans the data point
about a hundred and fifty times.

**Plan:** Catch `ConfigError` in `start()` and answer 400 with its message (or
add it to the application handler `API-R06` plans); build the member literal in
the column's dtype; put the chained cause's message in the failure; collect the
three derived frames with one `collect_all` or fold them into one aggregation.

**Acceptance:** An invalid card answers 400 naming the field; a tz-aware member
filter returns its row (the probe's shape as a test); a formula failure names the
refused construct; a relationships question performs one collect per feature.

**Dependencies:** None.

**Evidence:** `src/haute/routes/_pivot_service.py::PivotService`;
`src/haute/routes/_pivot_service.py::_member_literal`;
`src/haute/routes/_pivot_service.py::_formula_expression`;
`src/haute/routes/_explore_relationships.py::_categorical_levels`;
`src/haute/routes/explore.py`.

### EDA-R02 — Card fields, stale statements, the partition floor

**Why:** Three absent card fields (`sort`, `sort_rows`, `color_scale`) are
materialised with defaults while their siblings are required, so a card missing
`color_scale_split_by` is rejected and one missing `color_scale` is silently
completed, against the canonical-only policy and the specification's "rejected
rather than migrated". Two statements of the Explore specification contradict
the code: the "single aggregation guarantee" (the profile collects once per
batch of eight columns plus the histogram and duplicate-count passes, which its
own test counts) and the decorator's kwarg order, which omits `pivot_formulas`.
The duplicate-count scratch floors its partition size at one row, so a small
memory headroom turns a large frame into up to one partition per row.

**Plan:** Require the three fields like their siblings (the writers already emit
them) or document the exception; delete or restate the two specification
sentences; floor the partition size at tens of thousands of rows and let the
memory limit fail the job instead.

**Acceptance:** A card missing `sort` is refused like one missing
`decimal_places`; the specification matches the collect count and the kwarg
order; the scratch writes a bounded number of partitions.

**Dependencies:** None.

**Evidence:** `src/haute/_explore_pivots.py`;
`src/haute/_frame_profile.py::_count_unique_rows`;
`src/haute/_codegen_builders.py`; `specs/explore-eda/low-level.md`.

### EDA-R03 — The Explore panels' empty state, observer, glyph and sentinel

**Why:** A pivot result with no row paths (a filter matching no rows with row
grand totals off) makes the chart card show a danger alert saying the result
lacks the coordinates a chart requires and the configuration should be reviewed,
although nothing is misconfigured, while the Pivots pane shows a neutral "No rows
match" for the same result. The theme and reduced-motion observer effect is
duplicated line for line between Explore's combo chart and the workbench's chart.
Null cells render a hyphen where the specification says an em dash and the
Segments tab uses one. A pivot start failure is recorded by fabricating a job
entry with a time-based id and then failing it. Five Explore exports are
referenced only at their definitions and several more only inside their module.

**Plan:** Return an empty dataset for an empty result and keep the error for a
result with cells but no coordinates; one `useChartEnvironmentRevision` hook
beside the shared chart theme; one glyph; a store action that records a start
failure without a job id; delete and un-export the symbols.

**Acceptance:** An empty filtered pivot shows the pane's empty wording on its
chart; jscpd reports no observer pair; the glyph matches the specification;
knip reports none of the exports.

**Dependencies:** `FSH-R06` removes the store's other fabricated sentinel.

**Evidence:** `frontend/src/panels/explore/chartData.ts::adaptPivotChartData`;
`frontend/src/panels/explore/ExploreChartsPane.tsx`;
`frontend/src/panels/explore/ComboChart.tsx`; `frontend/src/workbench/ChartBody.tsx`;
`frontend/src/panels/explore/PivotTableGrid.tsx`;
`frontend/src/panels/explore/useExplorePivotActions.ts`;
`frontend/src/panels/explore/pivotConfig.ts`;
`frontend/src/panels/explore/chartConfig.ts`.

### EDA-E18 — Advanced Excel pivot parity

**Why:** Excel includes valuable higher-order pivot operations, but adding them
without representative use would multiply schema, execution, and UI complexity
without evidence that the additional surface is warranted.

**Plan:** Use real pivot layouts and cardinality metrics to prioritise date
grouping, per-level subtotals, Show Values As, calculated fields/items, slicers,
conditional formatting, and richer number formats. Each selected feature
receives its own spec amendment and bounded-execution contract; unselected
features remain explicitly unsupported rather than partially emulated.

**Acceptance:** A decision record ranks the advanced operations using usage,
scale, accessibility, and execution-cost evidence, and creates bounded child
packages only for selected work.

**Dependencies:** The current bounded pivot configuration/result contract plus
representative production evidence.

**Evidence:** `src/haute/routes/_pivot_service.py`; `frontend/src/panels/explore/pivotConfig.ts`; `frontend/src/panels/explore/ExplorePivotsPane.tsx`; `tests/test_explore_pivots.py`; pivot configuration/result telemetry and support feedback.

### EDA-E23 — Shared PivotChart interactions

**Why:** Excel-style chart filtering and hierarchy navigation are useful only
when their effect on the associated pivot and every dependent chart is clear.
Private chart filters would create a second, divergent analysis definition.

**Plan:** After the linked ComboChart workflow has representative use, add
field/filter controls that edit the source pivot's persisted filters rather
than creating chart-local copies. Before committing, show the pivot name and
count/list of dependent charts that will become stale. Commit the pivot change
once, refresh it once, and update every dependent pivot table and chart from
the one new result. When the pivot contract supports hierarchy state, add
shared expand/collapse; if EDA-E18 admits slicers, allow one slicer to target an
explicit pivot and therefore all linked charts. Legend visibility remains
local ephemeral view state and is not confused with persisted pivot filtering.

**Acceptance:** Tests cover shared filter impact disclosure, cancel/confirm,
one graph history edit, one pivot job for many dependents, stale/fresh
transitions, failed refresh retention, hierarchy propagation, slicer target
isolation, keyboard interaction, and no private aggregation or filter cache.

**Dependencies:** The current source-linked pivot/ComboChart contract plus
representative interaction evidence; slicer work also depends on EDA-E18.

**Evidence:** `src/haute/routes/_pivot_service.py`; `frontend/src/panels/explore/ExploreChartsPane.tsx`; `frontend/src/panels/explore/ComboChart.tsx`; `tests/test_explore_charts.py`; `frontend/src/panels/explore/__tests__/ExploreChartsPane.test.tsx`; pivot/chart interaction telemetry and the relevant pivot editor, result-store, Charts pane, and end-to-end workflow tests.

### EDA-E24 — Broader PivotChart parity

**Why:** Excel exposes many chart and formatting operations, but supporting
them without evidence would expand the renderer, schema, and validation surface
speculatively.

**Plan:** Use representative layouts and interaction metrics to prioritise
pie/doughnut, additional area presets, reusable chart templates,
drill-through, and SVG/data export. Keep scatter, bubble, stock, and any chart
that requires row-level observations explicitly unsupported while the source
contract is a categorical pivot matrix. Each selected capability receives a
spec amendment, typed compatibility rules, accessibility requirements, and a
bundle/performance budget before implementation.

**Acceptance:** A decision record ranks candidates by analyst use, pivot-shape
compatibility, accessibility, bundle cost, and rendering scale, and creates
bounded child packages only for selected work. Unsupported kinds fail clearly
and never reinterpret pivot categories as row-level observations.

**Dependencies:** The current source-linked ComboChart contract, EDA-E23 where
the candidate crosses shared interactions, and representative use evidence.

**Evidence:** `src/haute/routes/_pivot_service.py`; `frontend/src/panels/explore/chartConfig.ts`; `frontend/src/panels/explore/ExploreChartsPane.tsx`; `tests/test_explore_charts.py`; chart configuration/render telemetry, export requests, support feedback, and measured browser/bundle performance.
