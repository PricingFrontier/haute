# Frontend Preview & Explore — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/DataPreview.tsx` | Virtualised preview table, frame selection, search, cell callbacks and value formatting. |
| `frontend/src/panels/PreviewPanelFrame.tsx`, `frontend/src/panels/PreviewPanelTabs.tsx` | Resizable/collapsible frame and generic ARIA tab strip with optional visible/assistive per-tab indicators. |
| `frontend/src/panels/previewPanelLayout.ts` | Shared preview-panel dimensions and header/action layout constants. |
| `frontend/src/components/ExecutionDiagnosticsSummary.tsx` | Actionable execution-diagnostic banner owned by [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/low-level.md) and consumed by Explore progress and cache reports. |
| `frontend/src/components/ExecutionDiagnosticsIndicator.tsx` | Compact preview-header execution diagnostic indicator. |
| `frontend/src/panels/ExplorePreview.tsx` | Explore run/cancel/store lifecycle and Preview/Overview/Pivots/Charts tab composition. |
| `frontend/src/api/types.ts`, `frontend/src/types/guards.ts`, `frontend/src/stores/useNodeResultsStore.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned Explore API contracts, runtime guards, and node-scoped report/pivot job/result state consumed by the preview panes. |
| `frontend/src/panels/UtilityPanel.tsx` | Utility-module list/read/create/delete/editor UI with debounced, flushable saves and syntax-error display. `App.tsx` loads the panel through a lazy import only after the user opens Utility, keeping its editor and API path out of startup JavaScript. |
| `frontend/src/panels/explore/cacheIdentity.ts` | Upstream-lineage/config identity for an Explore cache request. |
| `frontend/src/panels/explore/overviewCardDefinitions.ts`, `frontend/src/panels/explore/overviewConfig.ts` | Ordered overview-card registry and defensive config reader. |
| `frontend/src/panels/explore/ExploreOverviewPane.tsx` | Enabled-card/empty-state dispatcher. |
| `frontend/src/panels/explore/pivotConfig.ts`, `frontend/src/panels/explore/useExplorePivotActions.ts`, `frontend/src/panels/explore/useAutoUpdateExplorePivots.ts`, `frontend/src/panels/explore/ExplorePivotsPane.tsx`, `frontend/src/panels/explore/PivotTableGrid.tsx` | Pivot v0/v1 parsing, calculation identity, and the shared result-freshness predicate; shared table/chart run and cancel lifecycle; deduplicated automatic scheduling for mounted consumers; enabled-section lifecycle; virtualised semantic matrix rendering. |
| `frontend/src/panels/explore/ExploreResultCardChrome.tsx` | Result-card chrome shared by the Pivots and Charts panes: the centered empty state and the Cancel/Starting/Retry run-status action cluster. |
| `frontend/src/panels/explore/chartConfig.ts`, `frontend/src/panels/explore/chartData.ts`, `frontend/src/panels/explore/chartOptions.ts`, `frontend/src/panels/explore/chartRuntime.ts`, `frontend/src/panels/explore/ComboChart.tsx`, `frontend/src/panels/explore/ExploreChartsPane.tsx` | Versioned chart parsing/linkage/presets; pure typed pivot adapter; safe renderer options; narrow ECharts registration/lifecycle/accessibility; enabled-card state dispatch. |
| `frontend/src/panels/explore/ExploreSummaryCards.tsx`, `frontend/src/panels/explore/SchemaTableCard.tsx` | Dataset, quality, numeric, categorical and schema report cards, including card-specific export grids. |
| `frontend/src/panels/explore/ExploreTableActions.tsx` | Read-only copy-as-TSV and download-as-CSV actions for supported Explore tables, built on the shared table serializers. |
| `frontend/src/panels/explore/DistinctInfoButton.tsx`, `frontend/src/panels/explore/StatValueCell.tsx` | Distinct-count explanation and reusable optional-stat cell. |

## Key types and data structures

- `PreviewData` in `frontend/src/panels/DataPreview.tsx` carries status, schema, preview rows,
  optional frame schema/selection and execution diagnostics. The table combines preview columns
  with selected-frame/flat schema so a returned value is never omitted merely for missing dtype.
- `OverviewConfig` is `Partial<Record<OverviewCardKey, boolean>>`; the fixed
  `OVERVIEW_CARD_DEFINITIONS` order is authoritative regardless of raw-object key order.
- Explore jobs/results in `frontend/src/stores/useNodeResultsStore.ts` are accepted only when
  a cached result's stored `configHash` matches `frontend/src/panels/ExplorePreview.tsx`'s newly
  calculated canonical identity. Active jobs are node-owned and remain actionable across
  identity changes.

## Control flow

### Preview table

1. `frontend/src/panels/DataPreview.tsx` derives a lower-cased column search index when columns
   change and filters it without rebuilding that index per keystroke.
2. A `ResizeObserver` and scroll handler determine the row/column windows. Scroll updates are
   coalesced to animation frames; row virtualisation begins after 50 rows and horizontal windows
   render spacer cells for skipped columns.
3. One delegated tbody click handler reads row/column dataset attributes and calls the supplied
   trace callback. Embedded mode omits outer frame chrome; normal mode uses the shared frame.

### Explore and overview

1. `frontend/src/panels/explore/cacheIdentity.ts` finds all upstream nodes, removes Explore
   overview, pivot, and chart display settings from data-affecting config, and includes
   submodels/preamble.
2. `frontend/src/panels/ExplorePreview.tsx` canonicalises that identity together with the active
   source. It posts that graph identity to `/api/explore/cache-status` on mount and whenever the
   identity changes; a rerender that preserves the identity (for example a canvas drag) does not
   re-post. A `current` response hydrates the completed result store without a run; a
   `stale` response controls the warning action state but is not installed as a current report.
   It ignores retained frontend results with a different identity, but keeps them as evidence of
   staleness and keeps the node's active job visible and cancellable using the source that job
   actually started with. It records immediate cache hits as completed results and background
   starts as jobs.
3. Start failures, and cancellation responses without a completed report, call the result-store
   failure path; thrown start/cancel errors also toast. `useBackgroundJobs` in frontend-shared
   polls background Explore and pivot jobs and moves terminal responses into the result store. A visible
   report is touched to update cache recency. Preview, Overview, Pivots, and Charts mount only for their
   active tab; a remembered value from a still-unsupported pane normalises to Preview.
   `ExploreOverviewPane` is a `React.lazy` boundary, so its report-card and export code stays out
   of startup JavaScript. Suspense renders a labelled Overview loading state inside the existing
   tabpanel until that module is ready.
4. When idle, the cache action derives one of three states from the identity-gated result, retained
   stale result, and backend inspection: `missing` renders a filled red `Needs caching` button;
   `current` renders a filled green `Re-cache` button; `stale` renders a filled yellow `Re-cache`
   button. The subtitle mirrors `Needs caching`, `Cached`, or `Cache stale`. Clicking either
   Re-cache state sends `refresh: true`; clicking Needs caching normally sends `refresh: false`.
   After an inspection error it sends `refresh: true`, providing an explicit recovery path that
   bypasses a corrupt selected generation. The status request is abortable and an obsolete
   identity response cannot hydrate the new identity. A
   resolved backend `missing` state is authoritative over an old in-browser report; changing to a
   source with no generation therefore shows red, while a same-family identity mismatch reported
   by the backend shows yellow. A stale, missing, or failed inspection suppresses the retained
   report from dependent panes. Inspection failure also returns the action to red and emits an
   explicit error toast rather than leaving an unverified green state.
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
   Values, an uncalculated card, a submitting request, a running job, fresh and stale retained
   results, and per-card failures. A running refresh may keep its retained table visible while the
   card exposes progress and Cancel. Cache-key mismatch, calculation-identity mismatch, or an
   absent report marks a retained result stale; only an absent retained result uses the
   uncalculated guidance.
4. A card without Values is excluded from automatic calculation. Otherwise automatic calculation
   and failure-only Retry share explicit handling for cache-required, completed-with-result, and
   started-with-job responses. A completed response without a result, a started response without
   a job id, and a rejected request become retained terminal failures and always clear submitting
   state. Automatic effect re-renders and several consumers of one Pivot are deduplicated, so the
   same identity/cache pair is attempted once rather than entering a retry loop. The result store
   records the last attempted calculation and dataframe-cache identities separately from any
   retained successful result identity; remounting does not repeat a failed pair, while a new
   calculation or cache identity is automatically eligible. Cancel completion stores a returned
   result; every other terminal cancellation fails the job; a rejected cancellation keeps the
   active job for polling and shows a card-local notice.
5. `PivotTableGrid` receives an already guarded version-1 matrix and current Value presentation
   labels. It renders one semantic table, a header row for every configured Column level plus the
   Value level, sticky Row headers, explicit `Grand total` path labels, and null as an em dash.
   The scroll container row-window renders viewport rows plus overscan and uses spacer rows to
   preserve the complete scroll height. Horizontal overflow remains native so keyboard and
   assistive technology semantics are not replaced by a div grid.
6. Conditional formatting is keyed by stable Value placement id and never reorders or mutates the
   result. For each non-None scale, the grid takes finite numeric ordinary cells across all Column
   paths for that Value, excludes every grand-total row/column and blank/non-numeric cell, and uses
   the minimum, median (Excel-style 50th-percentile yellow midpoint), and maximum as its domain.
   It interpolates pale red–yellow–green or the reversed endpoints while retaining dark readable
   text. An equal-valued domain renders yellow; an empty numeric domain renders no formatting.

### PivotChart results

1. The Charts pane parses current v1 charts and pivots, reads existing composite pivot job/result
   entries, and resolves each enabled card independently. On mount it automatically schedules
   each distinct stale or missing configured source Pivot once through that Pivot's existing
   run/status lifecycle; charts sharing a source never create duplicate requests, and every
   scheduler consumer (Pivots pane, Charts pane, chart Configure editor) takes the store's
   atomic per-pivot claim before submitting — the claim records the target dataframe cache key,
   calculation identity, and a generation token; an identical-target attempt while it is held
   is a no-op, a newer target replaces it atomically, and only the current token may promote it
   to the job entry on submission or release it on failure, so superseded outcomes are
   discarded rather than overwriting newer work. Running work
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
4. The production bundle gate rejects any startup preload of the chart pane/runtime/vendor,
   caps the narrowly imported `vendor-charts` chunk at 205 KiB gzip, and keeps the measured
   application limits at 258 KiB initial and 1,300 KiB total gzip. Chart capability therefore
   pays its cost only after Charts is opened and cannot quietly grow inside the aggregate budget.

`DataPreview` consumes guarded version-1 execution metrics through
`ExecutionDiagnosticsIndicator`: projected/admitted/not-planned states stay
silent; a boundary or rejection places a warning/error icon immediately after
the row/column summary, and memory pressure uses the warning path. Activating
the icon explains projection limits, correctness, possible I/O/memory cost,
and remediation without exposing raw bounded-collection JSON.
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
- Explore refuses run while there is no input or a job is active. An instant completed response
  without a report, or a started response without `job_id`, is an explicit failure rather than a
  false success.
- A valid empty/missing chart array prompts the user to add a chart. A non-empty array with every
  card disabled reports that no charts are shown. Duplicate/blank ids and wrong-typed fields are
  invalid configuration, not empty state.
- Explore progress is a determinate ARIA progressbar only while the run is busy. Its value is
  the displayed fraction clamped to 0-100; native buttons give export actions keyboard semantics,
  and both are disabled when their grid has no body rows.
- Utility save replies cannot clear/show errors for a different active module. A 400 API detail
  matching `line N` highlights that line; list/load errors are toast-visible, not interpreted as
  a missing utility directory.
- The frame restores its saved height after expand-to-top and uses parent height, own bottom edge,
  then viewport height when measuring available space.
- `PreviewPanelTabs` gives exactly one enabled tab `tabIndex=0`; Left/Right wrap across enabled
  tabs, Home/End select the boundary tab, and disabled tabs are skipped.

## Error handling

Preview `loading` and `error` statuses are ordinary render branches. Explore records a terminal
error for a failed start or a cancellation that does not return a completed report; thrown
start/cancel exceptions also toast. Actionable cache-report execution metrics render in the
shared diagnostics banner. Utility syntax errors remain inline and block a requested file switch;
other file-operation failures toast or display action-local text. Cache/report/card shape is
assumed to meet the API contract; optional overview settings alone are parsed defensively.
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
delegation, boundary/rejected execution diagnostics, cache identity/result/job lifecycle,
overview/chart card ordering and config, chart list/configure/back/toggle behavior, chart
visualisation empty/error states, roving-tab accessibility, utility save-flush/stale-response behaviour and
syntax errors. The Explore suites also pin progressbar name/value semantics, TSV headers and
contents, RFC-4180 CSV quoting through the download blob, full filtered-schema export across
pagination, disabled empty-table actions, and native-button accessibility.
`frontend/src/__tests__/App.utilityPanelLazy.test.ts` and the bundle-budget tests
guard the Utility panel's on-demand chunk boundary. Shared layout/constants and small visual
helpers are exercised through these component tests rather than owning standalone suites.

Browser preview/smoke coverage is in `frontend/e2e/core-flows.spec.ts`,
`frontend/e2e/data-preview-scroll.benchmark.spec.ts`, and `frontend/e2e/smoke.spec.ts`.

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
