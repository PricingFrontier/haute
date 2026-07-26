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
  exposes Preview and Overview tabs, and uses a completed report only when its stored canonical
  identity matches the current graph identity. An active job remains visible and cancellable if
  the graph or active source changes while it runs.
- Overview cards have a fixed order and are individually enabled from config. They display
  dataset, quality, numeric, categorical and schema information with accessible empty states.
- Utility manages reusable utility Python modules, including parsed syntax failure locations.
  A failed autosave or flush preserves the dirty draft and blocks file switching until a later
  save succeeds. Shared preview chrome supports resizing, collapse and keyboard-accessible
  roving tabs.
- Preview and Explore reports surface actionable execution-memory or rejected-strategy
  diagnostics without replacing the primary data/status content.

## Design rationale

Virtualisation and delegated cell events make tabular inspection remain responsive at large
dimensions. Cache identity excludes view-only Explore overview toggles so changing displayed
cards does not invalidate an otherwise reusable report. Cached reports are identity-gated, while
running jobs are node-owned so a changed editor cannot strand their Cancel action. Utility-module
saves separately guard stale responses after an awaited request.

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
identity change. Invalid optional overview configuration is discarded while parsing, while
malformed data that a renderer cannot safely interpret is allowed to surface rather than being
fabricated.

## Execution diagnostics

`DataPreview` places actionable boundary, rejected-strategy, and memory-pressure detail behind an
accessible status icon beside the row/column summary. `ExplorePreview` renders actionable
execution metrics from progress or the completed cache report as a compact banner with technical
detail on demand. Missing or unsupported strategy payloads currently add no secondary diagnostic;
the primary preview/Explore error and status remain visible and no result is invented.
