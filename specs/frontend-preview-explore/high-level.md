# Frontend Preview & Explore — High-Level Specification

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

- Data previews render loading, error and successful data, support column search, selected-frame
  switching, trace-cell clicks, and virtualise large row/column grids.
- Explore computes a source- and lineage-sensitive cache identity, starts/cancels an Explore run,
  exposes Preview, Overview, Pivots, and Charts tabs in that order, and uses a completed report only when its stored
  canonical identity matches the current graph identity. An active job remains visible and
  cancellable if the graph or active source changes while it runs.
- Overview cards have a fixed order and are individually enabled from config. They display
  dataset, quality, numeric, categorical and schema information with accessible empty states.
  Schema, numeric-summary, and categorical-summary tables expose native-button actions to copy
  their tabular contents as TSV and download them as CSV. Schema export follows the active
  search filter but is independent of the visible pagination page, so a filtered wide schema is
  exported in full. Empty tables keep both actions visible but disabled. The optional Overview
  card implementation, including its action bars, is loaded on demand only when Overview is
  selected; the pane exposes a labelled loading state during that short module load.
- Schema rows and their exports include concise profile cues supplied by the report: identifier
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
- Pivots is lazy-loaded. Enabled cards render in persisted order as independent full-width
  sections; disabled cards are hidden without deleting retained results. Distinct empty,
  all-disabled, malformed, unconfigured, cache-required, loading, stale, error, and fresh states
  tell the analyst what to do next.
- Each configured pivot has its own Update/Cancel lifecycle keyed by Explore node plus pivot id.
  A calculation-config edit makes only that pivot stale; a changed/absent Explore dataframe-cache
  identity makes every dependent result stale/cache-required. A re-enabled unchanged pivot can
  immediately reuse its retained matching result.
- A fresh result renders in a horizontally scrollable, row-virtualised semantic table with
  multi-level column headers, sticky row headers, explicit grand-total labels, and typed cell
  display. One pivot's error never suppresses a successful sibling.
- The Explore run indicator exposes determinate `progressbar` semantics while a run is active,
  including a stable accessible name and a clamped percentage value from 0 through 100.
- Utility manages reusable utility Python modules, including parsed syntax failure locations.
  A failed autosave or flush preserves the dirty draft and blocks file switching until a later
  save succeeds. Shared preview chrome supports resizing, collapse and keyboard-accessible
  roving tabs.
- Preview places actionable projection-boundary, rejected-strategy, or memory
  pressure detail behind an accessible status icon beside the row/column
  summary. Explore uses a compact diagnostics banner for actionable progress
  or cache-report metrics. Missing/unsupported planner detail adds no invented
  secondary state; primary data, error, and status content remains authoritative.

## Design rationale

Virtualisation and delegated cell events make tabular inspection remain responsive at large
dimensions. Cache identity excludes view-only Explore overview, pivot-card, and chart-card settings so changing
displayed cards does not invalidate an otherwise reusable report. Cached reports are identity-gated,
while running jobs are node-owned so a changed editor cannot strand their Cancel action. Utility-module
saves separately guard stale responses after an awaited request. Explore table actions reuse the
shared clipboard and RFC-4180 CSV serializers so quoting behaviour cannot drift from editor
exports; exports contain the same display strings and headers as the corresponding card rather
than reconstructing raw data in the browser.

## Interactions

It uses graph, settings, UI and node-results stores, the API client, and shared diagnostics/UI
components. Its cell-click callback feeds [frontend-trace-ui](../frontend-trace-ui/high-level.md).
Explore configuration is edited by [frontend-node-editors](../frontend-node-editors/high-level.md).
[Frontend shared infrastructure](../frontend-shared/high-level.md) owns background polling and
advances Explore jobs to terminal result-store state.

## Failure model

Preview errors are normal rendered states. Explore start failures and cancellation responses that
do not include a completed report are recorded as terminal errors and surfaced to the user; a
cached report with a stale identity is not rendered, but an active node job is never hidden by an
identity change. Invalid optional overview configuration is discarded while parsing. Invalid
chart- or pivot-card configuration is surfaced in its pane rather than silently replaced, while
malformed data that a renderer cannot safely interpret is allowed to surface rather than being
fabricated.
