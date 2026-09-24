# Frontend Preview & Explore — High-Level Specification

After an upstream column selection changes, downstream previews automatically
discover the current output columns. Users do not need to restore deselected
columns merely to satisfy a remembered preview layout. A calculation that uses
a removed column still requires the user to update it or restore that column.

## Purpose

This component presents pipeline output before and after an Explore cache run: a large-table
preview, inspectable exploration cards, the utility view, and the shared panel frame and tab
controls used around those views.

## Scope

It owns presentation and client-side result lifecycle for data preview and Explore. It does not
execute pipelines or calculate profiling statistics: those are backend/execution responsibilities.
Modelling and optimiser result presentation belongs to
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md).

## Behaviour

- The node's Refresh action lives in the preview header immediately before the
  minimise/expand controls, including in the collapsed bar, rather than beside
  Close in the node editor. Data, Explore, modelling and optimiser previews use
  the same control. It refreshes the active node's preview and shared data cache
  as before, and remains available before results arrive. Submodels and their
  boundary ports do not offer Refresh; the schema-warning banner retains its
  separate Refresh and check action.
- Data previews render loading, error and successful data, support column search, selected-frame
  switching, trace-cell clicks, and virtualise large row/column grids. Row limits, column
  limits, and the table's rendering do not depend on where the rows came from.
- A preview's rows being computed from shared snapshots instead of recomputed is how the
  pipeline is meant to work, so the panel says nothing about it; its status row carries
  warnings and errors only. A preview is current
  only while no snapshot has been published, widened, refreshed, or cleared since it was
  requested: the displayed preview is then fetched again — a backend cache hit when its seeds
  did not change — and other nodes' stored previews when next displayed. A preview's own
  captures do not fetch it again unless something else changed while it was in flight.
- A trace carries the seed plan of the preview it explains. When that preview or the
  snapshots change, the displayed trace and highlight are hidden, an in-flight trace is
  aborted, and a late response is discarded; a trace whose snapshots have expired refreshes
  the preview.
- Explore reads the shared data point its node resolves to, exposes Preview, Overview, Pivots,
  Charts and Relationships tabs in that order, and renders statistics only from the shared profile of the data
  version that point currently holds. An active job remains visible and cancellable if the graph
  or active source changes while it runs.
- Explore shows the same cache state, the same progress, and the same one build as every other
  consumer of that data: opening Explore beside a Banding or Rating editor on the same input
  joins the running job rather than starting a second one, and one narrow generation can be
  current for Banding while it is only partial for Explore's wider column demand. The state
  survives a browser or backend restart, because it is the point's state and not the browser's.
- The header's shared data-cache indicator reports state without a separate build action: red `Not cached` when the
  point has no data, green `Cached` when the whole demand is cached, yellow `Cache out of date`
  or `Cached for some columns` when the data is stale or covers only some of the columns this
  consumer reads, and — while a build runs — progress with Cancel. Caching is what the node's own
  Refresh button does about data that is missing, stale, partial or unreadable; data that is
  already current is left alone, because the same button is pressed to re-read a node's generated
  fields. Refresh is also the recovery path from an unreadable snapshot. A point read straight
  from its Parquet file states that instead, having nothing to cache.
- The Explore preview's Relationships pane relates chosen features to a target and checks chosen
  key columns over the whole cached data; its choices are pane state, not config.
- Overview cards have a fixed order and are individually enabled from config. They display
  dataset, quality, numeric, categorical and schema information with accessible empty states.
  Schema, numeric-summary, and categorical-summary tables expose native-button actions to copy
  their tabular contents as TSV and download them as CSV. Schema export follows the active
  search filter but is independent of the visible pagination page, so a filtered wide schema is
  exported in full. Empty tables keep both actions visible but disabled. The optional Overview
  card implementation, including its action bars, is loaded on demand only when Overview is
  selected; the pane exposes a labelled loading state during that short module load.
- Schema rows and their exports include concise profile cues supplied by the profile: identifier
  candidate, high cardinality, text length range/mean, and temporal span. Exact duplicate-row
  findings appear through the existing Data Quality issue list.
- Charts is lazy-loaded and renders enabled PivotCharts in persisted order. Each independently
  resolves its selected pivot's retained result and renders explicit draft, missing-source,
  unconfigured, cache-required, loading, stale, pivot-error, adapter-error, render-error, or ready
  state. Several charts can share one pivot result without another calculation.
- Ready ComboCharts support column/line/area series, clustered and explicit stacks, primary and
  secondary axes, safe tooltips, deterministic colours, markers/labels, legend, bounds, theme and
  reduced-motion changes. Every card includes a textual summary and toggleable semantic data table;
  one card's error never hides successful siblings.
- Every rendered chart card offers a Download image action producing a 2× PNG of the current
  rendering over the chart's resolved theme background. The action is disabled while the card
  has no rendered chart data, and it fails loud: an SVG decode or canvas rasterisation failure
  surfaces a visible error message on the card and saves nothing — never a partial or blank
  file. The filename is the chart name lower-cased with every run of characters outside
  `a–z`/`0–9`/`-`/`_` collapsed to one `-`, trimmed of leading/trailing `-`, falling back to
  `chart`, plus `.png`.
- Pivots is lazy-loaded. Enabled cards render in persisted order as independent full-width
  sections; disabled cards are hidden without deleting retained results. Distinct empty,
  all-disabled, malformed, unconfigured, cache-required, loading, stale, error, and fresh states
  tell the analyst what to do next.
- Each configured pivot has its own automatic calculation/Cancel lifecycle keyed by Explore node
  plus pivot id. A mounted Pivots pane schedules each stale enabled pivot once; a mounted Charts
  pane does the same for each distinct stale source pivot used by an enabled chart; and an open
  chart Configure editor schedules its resolved source pivot the same way, so an already-stale
  source refreshes even while neither result pane is mounted (pivot structure itself is edited
  only in the Pivots editor). Concurrent scheduler consumers are
  serialised by the store's atomic per-pivot claim: exactly one submission per staleness target
  regardless of how many consumers are mounted, a newer dataframe/calculation target atomically
  replaces a held claim, and a superseded submission's outcome is discarded — an obsolete claim
  never blocks fresh work and a late old response never overwrites a newer job or result. A
  calculation-config edit makes only that pivot stale; a changed Explore dataframe-cache identity
  makes every dependent result stale, while an absent report waits for cached Explore data. A
  re-enabled unchanged pivot immediately reuses its retained matching result. A terminal failure
  stays local and exposes Retry; normal successful use has no extra refresh button.
- Selecting Pivots or Charts in the lower preview aligns the node panel's editor to the
  matching pane; Preview and Overview selections leave the editor untouched, and editor-side
  pane selections, Configure-subview entry, and Back never change the preview pane. Each chart
  card in the Charts
  pane offers a Configure action that opens the node panel's Charts editor directly at that
  chart's Configure subview.
- A fresh result renders in a horizontally scrollable, row-virtualised semantic table with
  multi-level column headers, sticky row headers, explicit grand-total labels, and typed cell
  display. Per-placement numeric formatting covers General, Number, Percentage, GBP/USD/EUR
  currency, optional thousands separators, and Automatic or fixed 0–10 decimal places for finite
  numeric Column members, Row members, and Value cells. Configured Row/value sorting is already
  reflected by result order. A per-Value
  prominent Excel-style red–yellow–green three-colour scale applies only to ordinary finite
  numeric body cells for that placement. By default its domain spans the entire Value; an optional
  split by a placed Row or Column field instead computes an independent domain for each distinct
  typed member of that field;
  blanks and grand-total row/column cells retain normal styling. One pivot's error never
  suppresses a successful sibling.
- The Explore run indicator exposes determinate `progressbar` semantics while a run is active,
  including a stable accessible name and a clamped percentage value from 0 through 100.
- Utility manages reusable utility Python modules, including parsed syntax failure locations.
  A failed autosave or flush preserves the dirty draft and blocks file switching until a later
  save succeeds. Shared preview chrome supports resizing, collapse and keyboard-accessible
  roving tabs.
- Preview places actionable projection-boundary, rejected-strategy, or memory
  pressure detail behind an accessible status icon beside the row/column summary.
  There are no cache quota refusals or quota-remediation messages.
  A successfully admitted materialisation boundary is informational and
  stays silent only when the same plan has no unprojected boundary and its execution
  metrics report no memory pressure. Mixed plans keep the real projection issue
  visible at the unprojected node. Explore
  uses a compact diagnostics banner for actionable progress or cache-report
  metrics. Missing/unsupported planner detail adds no invented secondary state;
  primary data, error, and status content remains authoritative.

## Design rationale

Virtualisation and delegated cell events make tabular inspection remain responsive at large
dimensions. The data a consumer reads excludes view-only Explore overview, pivot-card, and
chart-card settings, so changing displayed cards neither rebuilds the data nor recomputes its
profile. Statistics are gated on the data version they were computed from — a rebuild blanks the
panes rather than mislabelling the previous data's numbers — while running jobs are shared and
node-owned so a changed editor cannot strand their Cancel action. Utility-module
saves separately guard stale responses after an awaited request. Explore table actions reuse the
shared clipboard and RFC-4180 CSV serializers so quoting behaviour cannot drift from editor
exports; exports contain the same display strings and headers as the corresponding card rather
than reconstructing raw data in the browser.

## Interactions

It uses graph, settings, UI, node-results and node-data stores, the API client, and shared
diagnostics/UI components. Its cell-click callback feeds
[frontend-trace-ui](../frontend-trace-ui/high-level.md). Explore configuration is edited by
[frontend-node-editors](../frontend-node-editors/high-level.md).
[Frontend shared infrastructure](../frontend-shared/high-level.md) owns the shared data-cache and
profile hooks, the shared cache control, background polling, and advancing jobs to terminal
result-store state.

## Failure model

Preview errors are normal rendered states. A failed build or profile request is surfaced to the
user and leaves the panes showing the state the point reports; a profile computed from another
data version is not rendered, but an active job is never hidden by an identity change. A failed
point inspection is surfaced rather than being mistaken for a green hit, an identity change
aborts an in-flight inspection, and a document-fence change prevents a late reply from writing
state. Invalid optional overview configuration is discarded while parsing. Invalid
chart- or pivot-card configuration is surfaced in its pane rather than silently replaced, while
malformed data that a renderer cannot safely interpret is allowed to surface rather than being
fabricated. A preview that read no snapshot shows no cached data label, and a refetch after a
snapshot change that fails shows the ordinary preview error.
