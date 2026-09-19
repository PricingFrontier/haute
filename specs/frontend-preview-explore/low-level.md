# Frontend Preview & Explore — Low-Level Specification

## Preview column freshness

- Column metadata captured by `usePipelineAPI` records the request's structural
  version as `_columnsStructuralVersion`, alongside `_columnsSource`. Both are
  transient result metadata and do not change graph identity or persisted state.
- Preview requests may seed `requestedPreviewColumns` from cached columns only
  when their structural version and source match the request. Missing provenance
  or a mismatch requires schema discovery by omitting that optional projection.
  This applies to direct, recovery, downstream propagation and upstream refresh
  requests. Fresh schemas retain the initial preview column limit.
- Deselecting join columns must therefore let downstream pass-through previews
  discover the remaining columns without requesting the old schema. Actual code
  and join dependencies retain the backend's strict missing-column validation.
- Result-shape invalidation clears the structural version tag along with the
  source tag; selection-only edits still preserve the pre-filter column choices.

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/DataPreview.tsx` | Virtualised preview table, frame selection, search, cell callbacks and value formatting. |
| `frontend/src/panels/PreviewPanelFrame.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Resizable/collapsible frame and generic ARIA tab strip with optional visible/assistive per-tab indicators. |
| `frontend/src/panels/previewPanelLayout.ts` | Shared preview-panel dimensions and header/action layout constants. |
| `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Actionable execution-diagnostic banner owned by [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/low-level.md) and consumed by Explore progress and cache reports. |
| `frontend/src/components/ExecutionDiagnosticsIndicator.tsx` | Compact preview-header execution diagnostic indicator. |
| `frontend/src/panels/ExplorePreview.tsx` | Explore's shared-data-cache and profile composition: the data-cache action, the profiling progress, and the Preview/Overview/Pivots/Charts tabs. It owns no cache of its own. |
| `frontend/src/hooks/useNodeDataCache.ts`, `frontend/src/hooks/useNodeDataProfile.ts`, `frontend/src/components/DataCacheButton.tsx`, `frontend/src/components/dataCacheLabels.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned shared data-cache state, the shared `profile` analysis of the data a consumer reads, and the one cache control and its wording every consumer shows. |
| `frontend/src/panels/explore/exploreDataView.ts` | Adapts a point's profile into what the Explore panes render, and returns nothing when the profile does not describe the data version the point currently holds. |
| `frontend/src/stores/useNodeDataStore.ts` (`slotForConsumer`, `profileForConsumer`) | [frontend-shared](../frontend-shared/low-level.md)-owned reads of what a consumer node was last told it reads, answered only for the identity the answer was recorded under. |
| `frontend/src/api/types.ts`, `frontend/src/types/guards.ts`, `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/stores/useNodeDataStore.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned Explore API contracts, runtime guards, node-scoped pivot job/result state, and the slot-keyed data-point/profile state consumed by the preview panes. |
| `frontend/src/panels/UtilityPanel.tsx` | Utility-module list/read/create/delete/editor UI with debounced, flushable saves and syntax-error display. `App.tsx` loads the panel through a lazy import only after the user opens Utility, keeping its editor and API path out of startup JavaScript. |
| `frontend/src/panels/dataPointIdentity.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned upstream-lineage/config identity of the data a consumer reads, used to detect when the data a pane's results belong to has changed. |
| `frontend/src/panels/explore/overviewCardDefinitions.ts`, `frontend/src/panels/explore/overviewConfig.ts` | Ordered overview-card registry and defensive config reader. |
| `frontend/src/panels/explore/ExploreOverviewPane.tsx` | Enabled-card/empty-state dispatcher. |
| `frontend/src/panels/explore/pivotConfig.ts`, `frontend/src/panels/explore/pivotNumberFormat.ts`, `frontend/src/panels/explore/useExplorePivotActions.ts`, `frontend/src/panels/explore/useAutoUpdateExplorePivots.ts`, `frontend/src/panels/explore/ExplorePivotsPane.tsx`, `frontend/src/panels/explore/PivotTableGrid.tsx` | Pivot version-1 parsing, number-format helpers, calculation identity, and the shared result-freshness predicate; shared table/chart run and cancel lifecycle; deduplicated automatic scheduling for mounted consumers; enabled-section lifecycle; virtualised semantic matrix rendering. |
| `frontend/src/panels/explore/ExploreResultCardChrome.tsx` | Result-card chrome shared by the Pivots and Charts panes: the centered empty state and the Cancel/Starting/Retry run-status action cluster. |
| `frontend/src/panels/explore/chartConfig.ts`, `frontend/src/panels/explore/chartData.ts`, `frontend/src/panels/explore/chartOptions.ts`, `frontend/src/panels/explore/chartRuntime.ts`, `frontend/src/panels/explore/ComboChart.tsx`, `frontend/src/panels/explore/ExploreChartsPane.tsx` | Versioned chart parsing/linkage/presets; pure typed pivot adapter; safe renderer options; narrow ECharts registration/lifecycle/accessibility; enabled-card state dispatch. |
| `frontend/src/panels/explore/ExploreSummaryCards.tsx`, `frontend/src/panels/explore/SchemaTableCard.tsx` | Dataset, quality, numeric, categorical and schema report cards, including card-specific export grids. |
| `frontend/src/panels/explore/ExploreTableActions.tsx` | Read-only copy-as-TSV and download-as-CSV actions for supported Explore tables, built on the shared table serializers. |
| `frontend/src/panels/explore/DistinctInfoButton.tsx`, `frontend/src/panels/explore/StatValueCell.tsx` | Distinct-count explanation and reusable optional-stat cell. |
| `frontend/e2e/explore.spec.ts` | Explore browser journey: author/connect an Explore node, cache its data and reload, configure Pivots using the profile's schema, and observe fixed decimal formats in the calculated result. |

## Key types and data structures

- `PreviewData` in `frontend/src/panels/DataPreview.tsx` carries status, schema, preview rows,
  optional frame schema/selection, execution diagnostics, and the response's `seed_plan`: the
  shared-snapshot generations the rows were computed from, each `seeded` (read instead of
  computing the node) or `captured` (computed and written by this preview). The table combines preview columns
  with selected-frame/flat schema so a returned value is never omitted merely for missing dtype.
- `OverviewConfig` is `Partial<Record<OverviewCardKey, boolean>>`; the fixed
  `OVERVIEW_CARD_DEFINITIONS` order is authoritative regardless of raw-object key order.
- `ExploreDataView` in `frontend/src/panels/explore/exploreDataView.ts` is what every Explore
  pane renders: the profile's `row_count`, `column_count`, `columns` and `overview_summary`,
  the `data_version` they were computed from, and the `source` and `producer_node_id` the panes
  label them with. `exploreDataView(profile, dataVersion, producerNodeId, source)` takes only
  primitives and stored objects, so a caller can select them from the shared store without
  creating an object per render, and returns `null` when the profile's `data_version` differs
  from the point's — statistics are never shown beside another version's labels.
- Pivot jobs/results in `frontend/src/stores/useNodeResultsStore.ts` are accepted only when a
  cached result's stored `configHash` matches the newly calculated canonical identity. Active
  jobs are node-owned and remain actionable across identity changes.

## Control flow

### Preview table

1. `frontend/src/panels/DataPreview.tsx` derives a lower-cased column search index when columns
   change and filters it without rebuilding that index per keystroke.
2. A `ResizeObserver` and scroll handler determine the row/column windows. Scroll updates are
   coalesced to animation frames; row virtualisation begins after 50 rows and horizontal windows
   render spacer cells for skipped columns.
3. One delegated tbody click handler reads row/column dataset attributes and calls the supplied
   trace callback. Embedded mode omits outer frame chrome; normal mode uses the shared frame.
4. When any `seed_plan` entry is `seeded`, the status bar of an `ok` preview shows "Using cached
   data from" with those entries' node labels; a preview that read no snapshot — including one
   that only captured — shows no label.

### Explore and overview

1. `frontend/src/panels/ExplorePreview.tsx` holds no cache state of its own. `useNodeDataCache`
   resolves the node's shared data point for the active source and reports that point's
   availability for this consumer's column demand; `useNodeDataProfile` supplies the shared
   `profile` analysis of the point's current data version. A Banding or Rating editor on the same
   input therefore shows the same state and joins the same build rather than starting a second
   one.
2. `exploreDataView(profile, point.data_version, point.point.producer_node_id, activeSource)`
   builds what the Overview, Pivots and Charts panes render. Because it returns `null` unless the
   profile's data version is the one the point currently holds, a rebuild between two renders
   blanks the panes instead of attributing the previous data's statistics to the new data.
   `frontend/src/panels/dataPointIdentity.ts` remains the identity pivot and chart results are
   gated on; calculated-field definitions affect pivot calculations but never the data itself.
   The panes an editor renders read the shared store through `profileForConsumer`/
   `slotForConsumer`, which answer only for the identity the consumer is currently asking about:
   until the new point answers, a source switch or a rewiring leaves them with nothing rather
   than the previous point's statistics under the new labels.
3. The profile is asked for once per slot and data version, and only while the point is
   `current` — it describes the whole dataset, so it is never computed from partial data. The
   request carries a document-execution fence: a reply that arrives after the document has moved
   on is dropped rather than published. Whichever consumer asked, the running profile job is
   polled once for the whole application and its result is stored per slot, so every pane showing
   that data gets it, and its progress can be cancelled from any of them. A failure — of the
   request or of the job — is recorded against that slot and the data version it was asked for,
   shown in the frame's subtitle, and offered as a Retry action; nothing asks again on its own
   while it stands, so a failed profile is never an empty pane with no way forward (a
   directly-read point has no Re-cache action to recover through). A job's outcome belongs to the
   version *it* profiled, not to whatever the point holds when it ends, so a re-cache that
   published while it ran is still profiled rather than inheriting the older attempt's failure. A
   refused cancellation leaves the job running, cancellable, and its failure visible. Preview, Overview, Pivots and Charts mount only
   for their active tab; a remembered value from a still-unsupported pane normalises to Preview.
   `ExploreOverviewPane` is a `React.lazy` boundary, so its report-card and export code stays out
   of startup JavaScript. Suspense renders a labelled Overview loading state inside the existing
   tabpanel until that module is ready.
4. The cache action is the shared `DataCacheButton`: a filled red `Needs caching` for a point
   with no data, filled green `Re-cache` when the whole demand is cached, filled yellow
   `Re-cache` when the cached data is stale or covers only some of the columns this consumer
   reads, a muted `Checking cache` while the point is being resolved, and — while a build runs —
   a Cancel button with that build's determinate progress. `Needs caching` sends a plain build;
   every `Re-cache` state sends a refresh, which is also the recovery path from an unreadable
   snapshot. A point read straight from its Parquet file has nothing to build, so the control is
   replaced by `Reads Parquet directly`. The frame's subtitle states the same status in words,
   and while the profile runs it states the profile's progress instead.
5. `frontend/src/panels/explore/overviewConfig.ts` drops malformed config values. The overview
   pane renders no-enabled-cards, no-report, or the ordered enabled renderer set.
   `frontend/src/panels/explore/chartConfig.ts` instead returns an explicit parse failure for a
   malformed `charts` block. The Charts pane renders that diagnostic or resolves enabled charts
   against current pivots and retained pivot result state in persisted order.
6. Schema, numeric-summary, and categorical-summary cards derive a `TableGrid` from the exact
   display fields they render and pass it to `ExploreTableActions`. Copy serialises the header
   and every exported row as TSV; download uses the shared CSV escaping helper. Schema supplies
   every column matching its current search query, not only the current 50-row page. Numeric and
   categorical summaries export their complete profile lists. The actions do not offer JSON
   sharing or paste-in because Explore reports are read-only analysis artifacts. The shared
   serializers remain click-loaded from the already-lazy Overview code.
7. `SchemaTableCard` derives one factual Profile cell per column from the report's additive
   quality fields: ID candidate, high cardinality, text length min/mean/max, and temporal span.
   The same text participates in schema search and full filtered TSV/CSV export; an unflagged
   column renders an em dash.

### Pivot results

1. The Pivots pane parses current node config once and renders enabled pivots as full-width
   sections in persisted order. It keys `pivotResults` and `pivotJobs` by `${nodeId}:${pivotId}`.
   Automatic attempts are scoped to the current document execution generation.
   If document adoption invalidates an in-flight response, the current generation
   may submit again after the old claim is released. An unsynchronized or
   non-executable document must not claim work or consume an automatic attempt;
   calculation resumes when the document becomes executable. A real terminal
   failure still requires Retry or a changed calculation/cache identity.
   When a current Explore cache report exists, the mounted pane automatically sends every stale
   or uncalculated configured card to the dedicated endpoint once per node, pivot calculation
   identity, and dataframe-cache identity. A synchronous cache hit is stored immediately; a
   started job enters shared polling; cache/cardinality/config errors stay on that section. A
   running or submitting card is never started again. Cancel addresses only that section's job,
   and an idle terminal failure exposes an explicit Retry.
2. Freshness requires both the result's matching `dataframe_cache_key` and the stored frontend
   calculation identity matching the current card. Layout changes therefore stale one result,
   while an upstream/cache change stales all. Disabling changes no result-store entry, so
   re-enabling an unchanged card immediately reuses its retained result. When the retained Pivot
   result is absent and there is no current Explore report, the section explains that full data
   must be processed and cached; when a report exists it shows automatic calculation progress.
   When only the current Explore report is absent, a retained table remains visible but stale and
   no request is started.
3. The pane distinguishes malformed config, no cards, all-disabled cards, an enabled card without
   any Value or selected formula, an uncalculated card, a submitting request, a running job, fresh
   and stale retained results, and per-card failures. A running refresh may keep its retained table visible while the
   card exposes progress and Cancel. Cache-key mismatch, calculation-identity mismatch, or an
   absent report marks a retained result stale; only an absent retained result uses the
   uncalculated guidance.
4. Only a card without either a Value or selected formula is excluded from automatic calculation.
   Every configured card, including a formula-only card, follows the same automatic, manual, table,
   and chart lifecycle. Automatic calculation and failure-only Retry share explicit handling for
   cache-required, completed-with-result, and
   started-with-job responses. A completed response without a result, a started response without
   a job id, and a rejected request become retained terminal failures and always clear submitting
   state. Automatic effect re-renders and several consumers of one Pivot are deduplicated, so the
   same identity/cache pair is attempted once rather than entering a retry loop. The result store
   records the last attempted calculation and dataframe-cache identities separately from any
   retained successful result identity; remounting does not repeat a failed pair, while a new
   calculation or cache identity is automatically eligible. Cancel completion stores a returned
   result; every other terminal cancellation fails the job; a rejected cancellation keeps the
   active job for polling and shows a card-local notice.
5. `PivotTableGrid` receives an already guarded version-1 matrix and current placement presentation
   settings. It renders one semantic table, a header row for every configured Column level plus the
   Value level, sticky Row headers, explicit `Grand total` path labels, and null as an em dash.
   `general` with Automatic precision retains the existing raw typed display. `number` exposes an
   ordinary decimal representation; `percent` multiplies the source value by 100 and appends `%`;
   the three currency formats prefix `£`, `US$`, or `€`. Percentage Automatic uses up to two
   decimals, currency Automatic uses exactly two, and a fixed 0–10 precision overrides those
   defaults with matching trailing zeroes. `use_grouping` inserts or suppresses the `en-GB` comma
   thousands separator for every non-General format and for fixed-precision General output.
   Formatting applies by Column/Row path level or stable Value placement id only to finite numeric
   values; null, NaN, Boolean, text, and temporal values remain unchanged. Rounding is
   half-away-from-zero. Canonical decimal strings, exponent notation, and integers outside
   JavaScript's safe range are transformed as decimal text rather than first being coerced to a
   lossy `number`.
   The scroll container row-window renders viewport rows plus overscan and uses spacer rows to
   preserve the complete scroll height. Horizontal overflow remains native so keyboard and
   assistive technology semantics are not replaced by a div grid.
6. Conditional formatting is keyed by stable Value placement id and never reorders or mutates the
   result. For each non-None scale with no split, the grid takes finite numeric ordinary cells across
   all Row and Column paths for that Value. A `color_scale_split_by` Row or Column placement id instead
   partitions those cells by the selected path level's typed `{kind, value}` member and calculates an
   independent domain for every distinct member (the same member is pooled across other path levels).
   Split references are validated against the current placed axes rather than silently falling back
   to a global scale. When a retained result's axes predate a current placement move, split colouring
   is omitted until the matching result arrives rather than indexing the stale path shape. Every
   domain excludes grand-total row/column and blank/non-numeric cells, and
   uses the minimum, median (Excel-style 50th-percentile yellow midpoint), and maximum.
   It interpolates the prominent Excel-style red `#F8696B`, yellow `#FFEB84`, and green `#63BE7B`
   stops, or the reversed endpoints, while retaining dark readable text. The rule-editor preview
   consumes those same shared stops. An equal-valued domain renders yellow; an empty numeric domain
   renders no formatting.

### PivotChart results

1. The Charts pane parses current v1 charts and pivots, reads existing composite pivot job/result
   entries, and resolves each enabled card independently. On mount it automatically schedules
   each distinct stale or missing configured source Pivot once through that Pivot's existing
   run/status lifecycle; charts sharing a source never create duplicate requests, and every
   scheduler consumer (Pivots pane, Charts pane, chart Configure editor) and every manual Retry
   takes the store's atomic per-pivot start claim before submitting — the claim records the
   requested dataframe cache key, calculation identity, and a generation token; an identical
   automatic target while it is held is a no-op, every Retry and every newer automatic target
   replaces it atomically, and only the current token may promote it to the job entry on
   submission or release it, so superseded outcomes are discarded rather than overwriting newer
   work. Running work
   exposes Cancel and an idle terminal failure exposes Retry. Chart appearance, name, ordering,
   and visibility do not touch the calculation lifecycle and rerender from retained source data.
   Each chart card's header offers a Configure action that stores the chart's id as the node's
   configured chart and selects the node panel's Charts editor pane.
2. A source is fresh only when its retained result dataframe key matches the current Explore
   report and its stored frontend calculation identity matches the current pivot. A fresh source
   is adapted separately for each chart, allowing presentation differences without copying or
   recalculating the matrix. Each chart is reconciled above the adapter
   (`reconcileValueEncodings`) so a pivot Value added after chart creation renders as a series
   with seeded default styling rather than an error; reconciliation is render-scoped here and
   persists only through the editor's next committed chart edit. The adapter admits row
   grand-total paths only behind the `include_grand_total` opt-in and excludes column
   grand-total paths unconditionally, and `inherit` number formatting renders as the General
   locale format (grouped `en-GB`; at most two fraction digits at magnitude ≥ 1, at most four
   significant digits below 1, `0` at zero) across ticks, labels, tooltips, and the semantic
   table. Draft and missing ids are
   distinct and never use the first pivot.
3. `ComboChart` lazy-loads the registered ECharts core runtime, builds options solely from the
   closed adapter dataset/config — including horizontal orientation (category axis vertical,
   value axes horizontal), stacks on any mark, and pre-normalised 100% stack values — observes
   its container, resizes on geometry changes, and
   disposes on data replacement/unmount. The runtime wrapper additionally exposes
   `getDataURL()` (the SVG rendering as a data URL); ComboChart's Download image action decodes
   that SVG into an `Image`, paints a canvas of exactly twice the rendered width and height
   with the chart's resolved theme background token before drawing the image at 2×, and saves
   the canvas as `<sanitised chart name>.png` via a transient anchor. The action is disabled
   until the runtime has rendered; decode or rasterisation failure sets the card's visible
   error state and triggers no download. No new dependency is introduced and the code stays
   inside the lazy chart chunk. The chart-card grid auto-fits against the available pane
   width with a 28 rem target minimum rather than using a viewport breakpoint, so an open side
   panel cannot force unreadably narrow cards. The accessible summary and semantic table derive
   from the same dataset as the visual chart.
4. The production bundle gate rejects any startup preload of the chart pane/runtime/vendor
   and caps the narrowly imported `vendor-charts` chunk at 205 KiB gzip. The application
   bundle limits are defined by [engineering quality](../engineering-quality/low-level.md);
   chart capability pays its cost only after Charts is opened and cannot quietly grow inside
   the aggregate budget.

`DataPreview` consumes guarded version-1 execution metrics through
`ExecutionDiagnosticsIndicator`: projected/admitted/not-planned states and a
successfully admitted `materialisation-boundary` with no unprojected boundary stay
silent; an `unprojected-streaming-boundary` (including one carried in a mixed
materialisation plan) or rejection places a warning/error icon
immediately after the row/column summary, and memory pressure uses the warning
path (including when it accompanies materialisation). A `warned` conservative run
places the warning icon titled "Execution ran without a memory estimate", explaining
that the step ran under the run's full reserved memory envelope inside a hard-capped
worker; memory pressure on such a run appends the pressure message exactly once
rather than duplicating the strategy remediation, a terminal memory-limit failure
is reported instead of the warned strategy — in `ExecutionDiagnosticsIndicator`
too, where the pressure diagnostic's title wins over the warned strategy's while
the warned detail stays in the explanation — and the requested and blocking nodes
are promoted to the canvas warning state. Activating the icon
explains projection limits, correctness, possible I/O/memory cost, and
remediation without exposing raw bounded-collection JSON.
`ExplorePreview` passes progress or cache-report metrics to
`ExecutionDiagnosticsSummary`, whose technical detail is disclosed on demand.

### Utility editing concurrency

1. `frontend/src/App.tsx` mounts `UtilityPanel` behind a local `Suspense` boundary only while
   `utilityOpen`; the bundle checker treats its chunk as lazy-only and rejects startup preload.
2. `frontend/src/panels/UtilityPanel.tsx` loads files on mount and selects the first file when
   none is active. Switching files first awaits `flushSave()`; a pending/in-flight failure or an
   already-settled rejected draft stops the switch, and the draft plus inline error remain visible
   until a later save succeeds.
3. Edits debounce for 500ms. Unmount flushes any pending write fire-and-forget; post-await
   state updates verify both mounted state and the module still selected, dropping stale replies.
4. Delete explicitly cancels a pending save for the deleted file. Create refreshes the list,
   loads the new module and passes the server-returned import line back to the preamble owner.

## Edge cases and invariants

- A multi-frame preview may have no flat columns: selected-frame columns supply the visible schema
  and header count. Preview-only columns remain visible with an unknown/empty dtype.
- `null`/`undefined` display separately from Haute non-finite-float sentinel objects. Table
  windows clamp if a changed result becomes narrower while horizontally scrolled.
- The cache action is disabled while the point cannot be built or a build is already running. A
  profile response that is neither a completed result nor a started/joined job publishes nothing
  rather than a false success.
- A valid empty/missing chart array prompts the user to add a chart. A non-empty array with every
  card disabled reports that no charts are shown. Duplicate/blank ids and wrong-typed fields are
  invalid configuration, not empty state.
- Explore progress is a determinate ARIA progressbar only while the build or the profile is
  busy. Its value is the displayed fraction clamped to 0-100; native buttons give export actions keyboard semantics,
  and both are disabled when their grid has no body rows.
- Utility save replies cannot clear/show errors for a different active module. A 400 API detail
  matching `line N` highlights that line; list/load errors are toast-visible, not interpreted as
  a missing utility directory.
- Every preview pane (data, explore, modelling, optimiser) renders through `PreviewPanelFrame`,
  which takes no per-pane sizing props: all panes share `PREVIEW_PANEL_DIMENSIONS` (initial and
  minimum height). The only height ceiling is the space available in the parent column: the
  parent's height minus siblings that cannot shrink (banners), since the flex-growing canvas can
  yield all of its height. Dragging and expand-to-top share that ceiling, so a drag reaches the
  same top edge as the expand command. Without a measurable parent the ceiling falls back to the
  frame's own bottom edge, then the viewport height. The frame restores its saved height after
  expand-to-top.
- `PreviewPanelTabs` gives exactly one enabled tab `tabIndex=0`; Left/Right wrap across enabled
  tabs, Home/End select the boundary tab, and disabled tabs are skipped.

## Error handling

Preview `loading` and `error` statuses are ordinary render branches. A failed build or profile
request toasts and leaves the panes showing the state the point reports, never fabricated
statistics. Actionable profile execution metrics render in the shared diagnostics banner. Utility
syntax errors remain inline and block a requested file switch; other file-operation failures
toast or display action-local text. Point/profile/card shape is assumed to meet the API contract;
optional overview settings alone are parsed defensively.
Missing or unsupported execution diagnostics stay silent; the primary preview
or Explore failure remains authoritative and no diagnostic-unavailable success
state is fabricated.

## Testing

Tests live in `frontend/src/panels/__tests__/DataPreview.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelFrame.test.tsx`,
`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx`,
`frontend/src/panels/__tests__/ExplorePreview.test.tsx` and
`frontend/src/panels/__tests__/UtilityPanel.test.tsx`, plus the focused overview suites under
`frontend/src/panels/explore/__tests__/` and
`frontend/src/__tests__/editors/ExploreChartsConfig.test.tsx`. They cover virtualisation, frames, search, trace click
delegation, boundary/rejected execution diagnostics, the cached data label listing only
seeded nodes, pivot identity/result/job lifecycle,
overview/chart card ordering and config, the data-cache action and profile lifecycle, chart
list/configure/back/toggle behavior, chart
visualisation empty/error states, roving-tab accessibility, utility save-flush/stale-response behaviour and
syntax errors. The Explore suites also pin progressbar name/value semantics, TSV headers and
contents, RFC-4180 CSV quoting through the download blob, full filtered-schema export across
pagination, disabled empty-table actions, and native-button accessibility.
`frontend/src/__tests__/App.utilityPanelLazy.test.ts` and the bundle-budget tests
guard the Utility panel's on-demand chunk boundary. Shared layout/constants and small visual
helpers are exercised through these component tests rather than owning standalone suites.

Generic browser preview/smoke coverage is in `frontend/e2e/core-flows.spec.ts`,
`frontend/e2e/data-preview-scroll.benchmark.spec.ts`, and `frontend/e2e/smoke.spec.ts`.
Explore owns a dedicated browser journey in `frontend/e2e/explore.spec.ts`: it authors and
connects an Explore node, caches the whole dataset, reloads the application, opens Pivots,
asserts that the profile's post-code schema populates the field palette, commits a Pivot
with fixed decimal places for a Column, Row, and Value, and observes those formats in its calculated
result. Focused component tests additionally exercise rejected or
malformed build and profile responses and rejected cancellation so those failures cannot leave a
job or the cache action in a false-success state.

## Modelling config panes

The modelling pane behavior is defined by
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).
`frontend/src/panels/PreviewPanelTabs.tsx` is the generic tab-strip owner and accepts one
optional per-tab indicator descriptor with a semantic kind and accessible label. It renders a
compact visible mark/text without relying on colour alone and includes the indicator meaning in
the tab's accessible name or description. Callers that omit indicators render exactly as before.

Indicators do not change the active key, enabled-tab list, click behavior, `aria-controls`,
equal-width sizing, or the existing roving-focus contract: exactly one enabled tab is tabbable;
Left/Right wrap, Home/End choose the boundaries, and disabled tabs are skipped. Updating only an
indicator must not move focus or select a different tab.

`frontend/src/panels/__tests__/PreviewPanelTabs.test.tsx` proves warning and active indicators,
visible/non-colour-only output, assistive text, indicator-only rerenders, and unchanged mouse,
disabled-tab, ARIA and Arrow/Home/End behavior. The modelling and node-editor components are
recorded consumers in `specs/ownership.toml`.
