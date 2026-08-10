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
| EDA-E09 | Planned | P2 | Add bounded server-binned distributions. |
| EDA-E10 | Planned | P2 | Add one cached on-demand relationship/key-analysis service. |
| EDA-E14 | Planned | P2 | Establish the versioned pivot-card contract, toggles, and lower preview shell. |
| EDA-E15 | Planned | P2 | Add Excel-style Filter/Columns/Rows/Values authoring. |
| EDA-E16 | Planned | P2 | Add bounded, cached, cancellable pivot calculation. |
| EDA-E17 | Planned | P2 | Render every enabled pivot as a full-width result table. |
| EDA-E18 | Deferred | P3 | Add advanced Excel-parity pivot operations after the MVP is proven. |
| EDA-E19 | Planned | P2 | Establish versioned PivotChart cards linked to stable pivot identities. |
| EDA-E20 | Planned | P2 | Adapt typed pivot results into deterministic, bounded chart datasets. |
| EDA-E21 | Planned | P2 | Add PivotChart source selection and Excel-style ComboChart configuration. |
| EDA-E22 | Planned | P2 | Render accessible, responsive, independently stateful ComboCharts. |
| EDA-E23 | Deferred | P3 | Add shared PivotChart filtering and hierarchy interactions. |
| EDA-E24 | Deferred | P3 | Add broader PivotChart types, export, and advanced presentation features. |

## Planned improvements

Pivot delivery order is `EDA-E14` → `EDA-E15` → `EDA-E16` → `EDA-E17`.
`EDA-E18` remains deferred until the end-to-end MVP has usage and scale
evidence. These packages do not change the existing roadmap start package.

### Pivot product contract

- The right-side Pivots pane owns adding, toggling, naming, and configuring
  pivot cards. `Add Pivot` creates an enabled card; the card's primary action
  toggles visibility, while its separate `Configure` action cannot toggle it.
- The lower Explore result tabs are ordered Preview, Overview, Pivots, Charts.
  Every enabled pivot renders there in persisted card order. Disabled pivots
  retain their configuration and any still-valid cached result.
- Enabled pivots stack vertically at full width because pivot tables are
  commonly wider than chart cards. One pivot's invalid config or execution
  failure must not hide successful siblings.
- Pivot configuration is display analysis attached to the Explore node. It
  never inserts a group-by into the ordinary graph execution path. Calculation
  runs against the explicitly materialised Explore dataframe cache through an
  admitted, bounded job.
- The MVP includes a name, visibility, exact-member filters, ordered Columns
  and Rows, ordered Values with core aggregations, row/column grand totals,
  and the stacked result grid. Date grouping, subtotals, Show Values As,
  calculated fields/items, slicers, and conditional formatting are deferred
  to `EDA-E18`.

The version-1 persisted card shape is:

```json
{
  "version": 1,
  "id": "pivot_1",
  "name": "Claims by Region",
  "enabled": true,
  "filters": [],
  "columns": [],
  "rows": [],
  "values": [],
  "options": {
    "row_grand_totals": true,
    "column_grand_totals": true
  }
}
```

Each placed field has its own stable non-empty id and field name. Values also
carry an aggregation and display name so the same source field can appear
more than once with different calculations.

### EDA-E14 — Pivot contract and visible-card preview shell

**Why:** The current persisted pivot card has only an id, renders as a neutral
configuration card, and has no lower result pane. That cannot express the
agreed chart-like visibility behaviour or safely evolve into an Excel-style
layout.

**Plan:** Update the owning Explore/backend, node-editor, preview, graph-cache,
and API specifications first. Introduce one explicit v0-to-v1 migration at the
pivot-config trust boundary; it converts current `{id}` cards into versioned,
named, enabled cards with empty field zones and grand totals enabled. Extend
the frontend and backend validators with typed known fields, unique
case-insensitive names per Explore node, unique card ids, and preservation of
unknown simple-literal fields. `Add Pivot` writes a complete v1 card. Change
the primary card action to an accessible checkbox that toggles only
`enabled`, while `Configure` remains a sibling action. Add `pivots` to
`ExplorePreviewPane`, remove the obsolete `relationships` preview value, and
lazy-load an `ExplorePivotsPane` between Overview and Charts. Initially it
renders named placeholders for enabled cards plus distinct no-cards,
all-disabled, malformed, and unconfigured states.

**Acceptance:** Regression tests prove v0 migration, v1 validation,
codegen/parser round-trip, stable ids and ordering, future-field preservation,
Add/Configure/Back/toggle separation, the four-tab lower order, all-enabled
placeholder ordering, disabled-card exclusion, and the distinct empty/error
states. Toggle/name-only edits remain excluded from the Explore dataframe
cache and structural-execution hash.

**Dependencies:** Delivered Explore pivot-card persistence, chart-card toggle
pattern, lower preview tab framework, lazy editor boundaries, and display-only
cache exclusions.

**Evidence:** `src/haute/_explore_pivots.py`; `src/haute/_types.py`;
`frontend/src/panels/editors/ExplorePivotsConfig.tsx`;
`frontend/src/panels/explore/pivotConfig.ts`;
`frontend/src/panels/ExplorePreview.tsx`;
`frontend/src/panels/explore/ExploreChartsPane.tsx`;
`tests/test_explore_pivots.py`;
`frontend/src/__tests__/editors/ExplorePivotsConfig.test.tsx`;
`frontend/src/panels/__tests__/ExplorePreview.test.tsx`.

### EDA-E15 — Excel-style pivot field authoring

**Why:** A visible pivot is only useful once an analyst can build its layout
from available Explore fields without editing raw configuration.

**Plan:** Pass upstream field names and dtypes from `NodePanel` into
`ExplorePivotsConfig`. Replace the placeholder Configure view with a committed
pivot-name field, searchable dtype-labelled field palette, and ordered
Filters, Columns, Rows, and Values zones. Every operation must have keyboard
controls for Add to, move, reorder, and remove; accessible drag-and-drop may
enhance those controls but cannot be the sole interaction. Allow one source
field across different zones, reject an exact duplicate within Filters/Rows/
Columns, and allow repeated Values with distinct placement ids and
aggregations. Numeric fields default to Sum and non-numeric fields to Count.
Initial aggregations are Sum, Count, Average, Min, Max, Median, and Distinct
count. Persist configuration edits immediately for graph undo/redo, retain
missing upstream fields as explicit invalid chips, and expose `Update preview`
as the separate expensive-computation action.

**Acceptance:** Component and parser tests cover field search, dtype display,
add/move/reorder/remove by pointer and keyboard, duplicate rules, repeated
value fields, aggregation defaults/options, unique-name validation, stale
field diagnostics, preservation of unknown settings, and one graph history
step per committed edit. Configure navigation and field edits never change
the card's enabled state.

**Dependencies:** EDA-E14 and the delivered upstream-column derivation in
`NodePanel`.

**Evidence:** `frontend/src/panels/NodePanel.tsx`;
`frontend/src/panels/editors/ExplorePivotsConfig.tsx`;
`frontend/src/panels/explore/pivotConfig.ts`;
`frontend/src/__tests__/editors/ExplorePivotsConfig.test.tsx`.

### EDA-E16 — Bounded pivot calculation service

**Why:** Pivot calculation is an aggregation and can explode with
high-cardinality row/column fields. Running it through the ordinary Explore
execution strategy would recreate the unsupported/unbounded group-by failure
that the explicit materialisation boundary is meant to avoid.

**Plan:** Add dedicated run/status/cancel API and service surfaces for pivot
jobs. Resolve the existing Explore dataframe cache key and return a typed
`cache_required` result when the full dataset is not materialised. Against the
cached frame, apply exact-member filters, group by ordered row/column fields,
compute the configured value expressions, and assemble a typed structured
matrix with row paths, column paths, value identities, cells, total markers,
warnings, and execution metrics. Admit the work under the bounded Explore
analysis profile with cancellation checkpoints. Define specification-owned
limits for row groups, column groups, displayed cells, and filter-member
responses; exceeding one returns dimensions and remediation rather than
truncating. Cache each pivot result by dataframe cache key, calculation-only
config, and result-schema version. Exclude visibility, name, and formatting
from that calculation hash so toggling/renaming reuses a valid result. A
newer pivot request supersedes an older one for the same Explore node/source.

**Acceptance:** Backend tests cover cache-required, cache hit, cancellation,
supersession, malformed fields, exact-member filters, ordered multi-row and
multi-column grouping, every MVP aggregation, repeated measures, null/NaN
semantics, grand totals, deterministic ordering, typed cardinality rejection,
independent pivot-result keys, and reuse after toggle/name-only changes. No
test observes partial or silently truncated output.

**Dependencies:** EDA-E15; delivered dataframe execution cache, Explore
materialisation, admission, cancellation registry, job lifecycle, and cache
invariants.

**Evidence:** `src/haute/routes/explore.py`;
`src/haute/routes/_explore_service.py`; `src/haute/schemas.py`;
`src/haute/_cache.py`; `tests/test_explore_routes.py`.

### EDA-E17 — Pivot result tables and lifecycle

**Why:** Enabled pivots need an Excel-like result surface that can represent
multi-level headers and independent loading/error states; the flat
`DataPreview` contract cannot express that structure faithfully.

**Plan:** Add a lazy `ExplorePivotsPane` and dedicated virtualised
`PivotTableGrid`. Render enabled pivots in persisted order as full-width
sections with their name, fresh/stale/loading/error state, Update action,
multi-level column headers, sticky row headers, horizontal scrolling, and
grand-total markers. Extend the API client, runtime guards, background polling,
and node-results store with pivot jobs/results keyed by Explore node and pivot
id. A layout edit marks only that pivot stale; an upstream/source change
invalidates every pivot; disabling hides but retains a valid result; re-enable
shows it immediately when its calculation identity still matches. An error in
one pivot stays local to its section.

**Acceptance:** Frontend tests cover ordered stacking, independent results and
errors, stale/fresh identity, Update/cancel, disabled-result retention,
re-enable reuse, source/upstream invalidation, multi-level semantic headers,
keyboard/scroll behaviour, totals, runtime-guard rejection, and actionable
cache/cardinality errors. Production build and live `haute-demo` verification
exercise Add → Configure → Update → toggle → render for multiple pivots.

**Dependencies:** EDA-E14 through EDA-E16 and the delivered background-job
polling/result-store infrastructure.

**Evidence:** `frontend/src/panels/ExplorePreview.tsx`;
`frontend/src/panels/explore`; `frontend/src/api/client.ts`;
`frontend/src/api/types.ts`; `frontend/src/stores/useNodeResultsStore.ts`;
`frontend/src/panels/__tests__/ExplorePreview.test.tsx`.

### EDA-E18 — Advanced Excel pivot parity

**Why:** Excel includes valuable higher-order pivot operations, but adding
them before the bounded MVP is proven would multiply schema, execution, and UI
complexity without usage evidence.

**Plan:** After EDA-E17 ships, use real pivot layouts and cardinality metrics
to prioritise date grouping, per-level subtotals, Show Values As, calculated
fields/items, slicers, conditional formatting, and richer number formats.
Each selected feature receives its own spec amendment and bounded-execution
contract; unselected features remain explicitly unsupported rather than
partially emulated.

**Acceptance:** A decision record ranks the advanced operations using usage,
scale, accessibility, and execution-cost evidence, and creates bounded child
packages only for selected work.

**Dependencies:** Delivered EDA-E14 through EDA-E17 plus post-release evidence.

**Evidence:** Pivot configuration/result telemetry and support feedback after
the MVP is available.

PivotChart MVP delivery order is `EDA-E19` → `EDA-E20` → `EDA-E21` →
`EDA-E22`, after the pivot result contract in `EDA-E16` is stable. `EDA-E23`
and `EDA-E24` remain deferred until the linked ComboChart workflow has usage,
accessibility, bundle-size, and rendering-scale evidence. A chart never adds
another aggregation path: every chart consumes one pivot's latest successful
typed result.

### PivotChart product contract

- The right-side Charts pane owns adding, toggling, naming, and configuring
  chart cards. `Add Chart` creates an enabled version-1 draft; its primary
  action toggles only chart visibility, while `Configure` cannot toggle it.
- Configure first selects a source pivot from the same Explore node. The chart
  stores the pivot's stable id, never its display name or current result-cache
  key. Pivot rename and reorder therefore preserve the link. A missing pivot
  is an explicit broken reference and is never replaced with the first
  available pivot.
- A pivot's `enabled` state controls only whether its table appears in the
  lower Pivots pane. Configured hidden pivots remain valid chart sources. One
  successful pivot calculation can feed any number of independently configured
  charts without another dataframe scan, group-by, or chart-owned job.
- Chart appearance edits are presentation-only. They rerender locally and do
  not invalidate the Explore dataframe cache, pivot calculation cache, or
  structural-execution hash. A chart-level Update action delegates to the
  source pivot's existing run/status/cancel lifecycle.
- Pivot Filters determine the chart's data; Rows become ordered hierarchical
  category-axis paths; Columns split Values into ordered legend series; and
  Values provide the numeric series. Subtotals and grand totals are excluded
  by default. Pivot ordering is preserved rather than re-sorted by the chart.
- The MVP chart kind is a two-dimensional ComboChart. Each generated series
  can be a column, line, or area on the primary or secondary numeric axis;
  columns can be clustered or assigned to an explicit stack group. Presets
  include clustered columns, stacked columns, lines, column plus line, column
  plus line on a secondary axis, and stacked columns plus line.
- Pivot null cells render as gaps rather than invented zeroes. Non-finite or
  malformed numeric cells and chart cardinality limits produce actionable
  errors. Results are never silently truncated, downsampled, re-aggregated, or
  remapped to a different field or pivot.
- Configure exposes chart name, source pivot, inherited field mapping, preset,
  per-series mark/axis/stack, titles, legend, markers, labels, colours, numeric
  formats, and automatic/manual axis bounds. Source-pivot changes that would
  discard existing series mappings require confirmation and commit as one
  undoable graph edit.
- Enabled charts render in persisted order in the lower Charts pane. Each card
  owns its fresh, stale, loading, source-error, config-error, and render-error
  state so one failure cannot hide successful siblings.
- Reference behaviour follows Excel's associated PivotTable/PivotChart model,
  while Haute deliberately guarantees ComboChart support consistently:
  [PivotChart overview](https://support.microsoft.com/en-us/excel/overview-of-pivottables-and-pivotcharts),
  [combo chart types](https://support.microsoft.com/en-US/Excel/available-chart-types-in-office),
  and [secondary axes](https://support.microsoft.com/en-US/Office/excelexp/add-or-remove-a-secondary-axis-in-a-chart-in-excel).

The version-1 persisted chart shape is:

```json
{
  "version": 1,
  "id": "chart_1",
  "name": "Claims and Average Cost",
  "enabled": true,
  "pivot_id": "pivot_1",
  "kind": "combo",
  "category": {
    "source": "rows",
    "include_subtotals": false,
    "include_grand_total": false
  },
  "value_encodings": [
    {
      "id": "encoding_1",
      "value_id": "claim_count",
      "mark": "column",
      "axis": "primary",
      "stack_group": null,
      "color": null,
      "data_labels": false
    },
    {
      "id": "encoding_2",
      "value_id": "average_cost",
      "mark": "line",
      "axis": "secondary",
      "stack_group": null,
      "color": null,
      "data_labels": false,
      "markers": true
    }
  ],
  "series_overrides": [],
  "axes": {
    "primary": {
      "title": "Claims",
      "minimum": null,
      "maximum": null,
      "number_format": "inherit"
    },
    "secondary": {
      "title": "Average cost",
      "minimum": null,
      "maximum": null,
      "number_format": "inherit"
    }
  },
  "legend": {
    "visible": true,
    "position": "bottom"
  }
}
```

`pivot_id` is `null` only for a newly added editable draft. Each
`value_encoding` references the stable id of one pivot Value placement and
acts as the default for every series produced from that Value. When a pivot
has Columns, an exact generated series identity combines the Value id with
the typed column-member path. Optional `series_overrides` can style one of
those concrete series differently; a newly observed member uses the explicit
Value default and is surfaced as using that default rather than silently
inventing configuration.

### EDA-E19 — Versioned PivotChart contract and source linkage

**Why:** Existing chart cards persist only `{id, enabled}` and render generic
placeholders. They cannot identify their data source, survive pivot renames,
or distinguish an editable draft from a broken dependency.

**Plan:** Update the Explore/backend, codegen/parser, node-editor, preview,
graph-cache, and API specifications first. Introduce one explicit v0-to-v1
migration at the chart-config trust boundary. It preserves the current id,
enabled state, and future simple-literal fields while adding a unique default
name, `pivot_id: null`, `kind: "combo"`, empty encodings/overrides, and complete
category/axis/legend defaults. Extend frontend and backend validators with
typed known fields, stable non-empty nested ids, case-insensitive unique chart
names, and preservation of unknown simple-literal fields. Structural
validation accepts a null draft or non-empty pivot id; a separate resolver
reports unconfigured and missing references against the owning Explore node's
ordered pivots. `Add Chart` writes a complete v1 draft. The source picker lists
configured pivots regardless of their visibility and distinguishes ready,
hidden, stale, unconfigured, and errored sources. Pivot deletion is blocked
while dependent charts exist and lists those charts so the user can reassign
or remove them deliberately.

**Acceptance:** Regression tests prove v0 migration, v1 validation,
codegen/parser round-trip, nested-id and name uniqueness, stable ordering,
future-field preservation, null-draft handling, hidden-source selection,
rename/reorder stability, explicit missing-reference diagnostics, dependent
deletion protection, and Add/Configure/Back/toggle separation. Chart source,
name, and appearance edits remain excluded from dataframe/pivot cache keys and
the structural-execution version.

**Dependencies:** EDA-E14 and EDA-E15 for stable versioned pivot identities;
delivered chart-card persistence, toggle workflow, and display-config cache
exclusions.

**Evidence:** `src/haute/_explore_charts.py`; `src/haute/_types.py`;
`src/haute/_config_builder.py`; `src/haute/_codegen_builders.py`;
`src/haute/_cache.py`;
`frontend/src/panels/editors/ExploreChartsConfig.tsx`;
`frontend/src/panels/explore/chartConfig.ts`;
`tests/test_explore_charts.py`; `tests/test_explore_round_trip.py`;
`frontend/src/__tests__/editors/ExploreChartsConfig.test.tsx`.

### EDA-E20 — Typed pivot-result chart adapter and lifecycle

**Why:** A structured pivot matrix contains hierarchical row paths, column
paths, repeated Values, totals, and typed member keys. Flattening its display
labels ad hoc inside a renderer would create unstable series identities and
plausible-looking but incorrect charts.

**Plan:** Define a pure typed adapter from one successful pivot result plus
one v1 chart config to a renderer-neutral chart dataset. Rows become
multi-level categories. The ordered product of column leaf paths and pivot
Value placements becomes series, with a canonical identity derived from the
Value placement id and typed column-member path. Preserve raw numeric cells
separately from formatted tooltip/label text. Exclude total cells unless the
chart explicitly opts in, retain nulls as gaps, and reject non-finite numeric
values. Resolve `value_encodings`, exact `series_overrides`, dormant overrides,
and explicit defaults deterministically. Define specification-owned limits for
categories, series, rendered points, label length, and hierarchy depth; return
observed dimensions and pivot-filter remediation when exceeded. Chart state
references the source pivot result identity and exposes unconfigured,
cache-required, loading, fresh, stale, cancelled, pivot-error, adapter-error,
and ready states. Update/cancel operations reuse the pivot lifecycle; there is
no chart route, dataframe access, aggregation cache, or background job.

**Acceptance:** Pure adapter tests cover no Rows/Values, one and multiple row
levels, no and multiple column levels, repeated Values, stable typed series
keys, deterministic ordering, exact overrides, new/dormant dynamic members,
subtotals/grand totals, negative/zero/null/NaN/infinite cells, formats, stale
identities, and every cardinality rejection. Lifecycle tests prove that one
pivot result feeds several charts, a chart update delegates once to the pivot,
and appearance/toggle edits neither run nor invalidate calculation.

**Dependencies:** EDA-E16 typed pivot result and calculation identity; EDA-E19
chart/source contract.

**Evidence:** `src/haute/schemas.py`; `frontend/src/api/types.ts`;
`frontend/src/types/guards.ts`; `frontend/src/stores/useNodeResultsStore.ts`;
`frontend/src/panels/explore/chartConfig.ts`;
`frontend/src/panels/explore/chartData.ts`;
`frontend/src/panels/explore/__tests__/chartData.test.ts`.

### EDA-E21 — PivotChart and ComboChart configuration

**Why:** Analysts need to select an existing pivot and control how its Values
are presented without recreating Filters, Rows, Columns, Values, or
aggregations inside a second editor.

**Plan:** Replace the placeholder Configure view with chart name, required
source-pivot selector, inherited field-mapping summary, chart preset, generated
series table, axes, and appearance sections. With no pivots, explain that
charts require one and provide a view-state-only route to the Pivots pane.
Selecting a pivot seeds one explicit Value encoding per current pivot Value;
all begin as clustered columns on the primary axis until the user chooses a
ComboChart preset or changes an individual series. Presets include clustered
columns, stacked columns, lines, column plus line, column plus line on a
secondary axis, and stacked columns plus line. Each generated series exposes
column/line/area mark, primary/secondary axis, stack group, colour, marker, and
data-label controls. Before the pivot has a successful result, Value-level
defaults remain editable while exact column-member overrides explain that an
Update is required to discover concrete series. Add chart/axis titles,
automatic or validated manual bounds, inherited/override number formats,
legend visibility/position, and category-label rotation. Cheap committed
changes rerender immediately and participate in graph undo/redo. Changing a
populated chart's source requires confirmation and atomically replaces its
source-dependent encodings; Back never mutates or enables the chart.

**Acceptance:** Component tests cover no-pivot guidance, source-state labels,
hidden source selection, initial encoding seeding, every preset, per-series
mark/axis/stack controls, generated-series overrides, uncalculated-source
guidance, titles/legend/labels/formats/bounds validation, source-change cancel
and confirm, one undo step for the confirmed reset, future-field preservation,
and Configure/Back/toggle independence. Invalid known configuration remains
editable through a specific diagnostic and is never replaced with defaults
without an explicit migration or user action.

**Dependencies:** EDA-E15 pivot Value-placement identities, EDA-E17 pivot
result lifecycle, EDA-E19, and EDA-E20.

**Evidence:** `frontend/src/panels/NodePanel.tsx`;
`frontend/src/panels/editors/ExploreChartsConfig.tsx`;
`frontend/src/panels/explore/chartConfig.ts`;
`frontend/src/__tests__/editors/ExploreChartsConfig.test.tsx`;
`frontend/src/panels/__tests__/NodePanel.test.tsx`.

### EDA-E22 — Accessible, responsive ComboChart rendering

**Why:** A production ComboChart needs coordinated category/dual-value axes,
columns, lines, areas, legends, tooltips, resizing, theming, and accessible
alternatives. Extending the current decorative placeholder or duplicating the
repository's specialised one-off SVG charts would not provide a maintainable
general PivotChart engine.

**Plan:** Replace each enabled placeholder with an independently stateful
ComboChart fed only by the EDA-E20 adapter. Use a narrowly imported Apache
ECharts runtime (`echarts/core`) with Bar/Line, Grid, Dataset, Tooltip, Legend,
Title, DataZoom, ARIA, and SVG renderer modules. Load the runtime only through
the already lazy Charts pane, isolate it in a chart vendor chunk, and keep it
out of initial module preloads. Build options through a pure typed function;
persisted config cannot supply executable callbacks, HTML, URLs, or raw ECharts
options, and tooltips use safely encoded text. Register Haute light/dark theme
tokens, ResizeObserver-based sizing, deterministic disposal, reduced-motion
behaviour, high-contrast series differentiation, and axis rules that include
zero for automatically ranged column axes. Render ordered chart cards in the
existing responsive grid with per-card source/stale/loading/error actions.
Provide an accessible chart summary and toggleable semantic data table rather
than relying only on generated canvas/SVG descriptions. Add SVG/Canvas and
multi-card performance measurements before fixing the default renderer; use
the measured choice consistently rather than switching silently at runtime.

**Acceptance:** Renderer tests prove column/line/area combinations, clustered
and explicit stack groups, primary/secondary axes, negative and null values,
multi-level category labels, deterministic colours, markers, labels, legends,
tooltips, manual bounds, resize/dispose, dark/light/reduced-motion rendering,
safe hostile labels, semantic table equivalence, and independent sibling
errors. Bundle analysis proves no chart runtime in initial JavaScript, the
new lazy chunk and total output remain within repository budgets, and the UI
dependency audit passes. Live `haute-demo` verification exercises Pivot Add →
Configure → Update, Chart Add → Configure source/combo → Back → toggle,
two charts sharing one pivot, a hidden source pivot, and stale-source refresh.

**Dependencies:** EDA-E17 and EDA-E19 through EDA-E21.

**Evidence:** `frontend/package.json`; `frontend/package-lock.json`;
`frontend/vite.config.ts`; `frontend/scripts/check-bundle-size.mjs`;
`frontend/scripts/check-ui-dependencies.mjs`;
`frontend/src/panels/explore/ExploreChartsPane.tsx`;
`frontend/src/panels/explore/ComboChart.tsx`;
`frontend/src/panels/explore/chartOptions.ts`;
`frontend/src/panels/__tests__/ExplorePreview.test.tsx`;
`frontend/src/panels/explore/__tests__/ComboChart.test.tsx`.

### EDA-E23 — Shared PivotChart interactions

**Why:** Excel-style chart filtering and hierarchy navigation are useful only
when their effect on the associated pivot and every dependent chart is clear.
Private chart filters would create a second, divergent analysis definition.

**Plan:** After the ComboChart MVP is proven, add field/filter controls that
edit the source pivot's persisted filters rather than creating chart-local
copies. Before committing, show the pivot name and count/list of dependent
charts that will become stale. Commit the pivot change once, refresh it once,
and update every dependent pivot table and chart from the one new result.
When the pivot contract supports hierarchy state, add shared expand/collapse;
when `EDA-E18` admits slicers, allow one slicer to target an explicit pivot and
therefore all linked charts. Legend visibility remains local ephemeral view
state and is not confused with persisted pivot filtering.

**Acceptance:** Tests cover shared filter impact disclosure, cancel/confirm,
one graph history edit, one pivot job for many dependents, stale/fresh
transitions, failed refresh retention, hierarchy propagation, slicer target
isolation, keyboard interaction, and no private aggregation or filter cache.

**Dependencies:** Delivered EDA-E18 through EDA-E22 plus usage evidence from
the linked ComboChart workflow.

**Evidence:** Pivot/Chart interaction telemetry; relevant pivot editor,
result-store, Charts pane, and end-to-end workflow tests.

### EDA-E24 — Broader PivotChart parity

**Why:** Excel exposes many chart and formatting operations, but supporting
them before the source-linked ComboChart is proven would expand the renderer,
schema, and validation surface without evidence.

**Plan:** Use post-release layouts and interaction metrics to prioritise
horizontal bar, 100% stacked, pie/doughnut, additional area presets, reusable
chart templates, drill-through, and PNG/SVG/data export. Keep scatter, bubble,
stock, and any chart that requires row-level observations explicitly
unsupported while the source contract is a categorical pivot matrix. Each
selected capability receives a spec amendment, typed compatibility rules,
accessibility requirements, and bundle/performance budget before
implementation.

**Acceptance:** A decision record ranks candidates by analyst use, pivot-shape
compatibility, accessibility, bundle cost, and rendering scale, and creates
bounded child packages only for selected work. Unsupported kinds fail clearly
and never reinterpret pivot categories as row-level observations.

**Dependencies:** Delivered EDA-E19 through EDA-E23 plus post-release evidence.

**Evidence:** Chart configuration/render telemetry, export requests, support
feedback, and measured browser/bundle performance after the MVP is available.

### EDA-E09 — Distribution charts
**Why:** Analysts need distributions without client-side raw-data processing.

**Plan:** Emit capped server-binned numeric histograms from the bounded report path and render them with explicit empty/skipped states.

**Acceptance:** Tests cover null, constant, negative, and wide-schema guardrail cases plus chart rendering.

**Dependencies:** Delivered bounded collection and tab/panel contracts (formerly EDA-E03, EDA-E06, EDA-E07).

**Evidence:** `src/haute/routes/_explore_service.py`; `src/haute/schemas.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

### EDA-E10 — Target relationships
**Why:** A report needs bounded, target-aware signals for feature investigation.

**Plan:** After EDA-E09 establishes the bounded distribution primitives, add
one on-demand analysis job/cache surface with explicit cache-miss and
cancellation behaviour. It owns bounded numeric/categorical target
aggregations, target/weight configuration, and exact user-selected
multi-column key uniqueness checks. Key analysis is not a second synchronous
scan path or a base-report estimate.

**Acceptance:** Tests cover cache miss, cancellation/supersession, target and
weight validation, numeric and categorical results, bounded levels, ranked UI
rendering, exact single-/multi-column key counts, unhashable key rejection, and
cache identity for the selected analysis and columns.

**Dependencies:** EDA-E09 plus delivered bounded collection, dataframe-cache,
job-lifecycle, and tab/panel contracts (formerly EDA-E03, EDA-E06, EDA-E07,
EDA-E13).

**Evidence:** `src/haute/routes/explore.py`; `src/haute/routes/_explore_service.py`; `frontend/src/panels/explore`; `tests/test_explore_routes.py`.

## Delivered outcomes

- `EDA-E11` report schema v5 adds valid-value uniqueness ratios,
  high-cardinality and conservative identifier-candidate cues, min/mean/max
  text length, temporal span, and exact full-row duplicate count/ratio. These
  remain in the existing single cancellable aggregate; an Object column makes
  whole-row duplicates explicitly unknown rather than estimated. The Schema
  card renders, searches, copies, and downloads the factual cues. Backend,
  runtime-guard, and card regressions cover representative/null/boundary
  behaviour. User-selected multi-column key analysis is deliberately folded
  into EDA-E10's on-demand job/cache surface above, where its scan lifecycle
  can be explicit.
- Wide-schema search/pagination, sticky table headers, explicit column counts,
  semantic tables, and a clamped named Explore progressbar complete `EDA-E08`.
  `ExplorePreview.test.tsx` and the focused card suites pin the progress and
  navigation semantics.
- `EDA-E12` adds read-only native-button TSV copy and CSV download actions to
  Schema, Numeric Summary, and Categorical Summary. The actions reuse the
  shared serializers, disable on empty tables, use card-specific accessible
  names, and export every filtered schema row independent of pagination.
- Duration-safe value counts, truthful NaN/null statistics, one batched
  cancellable streaming collect with typed memory-limit outcomes, stat-gated
  input fingerprints, and lossy-decoded binary labels (`EDA-E01`–`EDA-E05`)
  are present-tense contracts in
  [the Explore specification](../explore-eda/high-level.md), with regressions
  in `tests/test_explore_routes.py`.
- The two content-backed Preview/Overview tabs (`EDA-E06`) and the
  hide-stale-reports panel design that superseded `EDA-E07`'s labelled-stale
  plan are specified in
  [the frontend preview/explore specification](../frontend-preview-explore/high-level.md).
- The quantile portion of `EDA-E11` (p25/median/mean/p75/std) remains part of
  the numeric profile.
- `EDA-E13` is covered generically by the shared dataframe execution cache and
  background-job lifecycle components, so no Explore-specific cache/job
  robustness work remains.
