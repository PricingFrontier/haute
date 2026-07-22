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
  exposes Preview and Overview tabs, and uses a completed report only when its stored cache hash
  and source match the current graph identity.
- Overview cards have a fixed order and are individually enabled from config. They display
  dataset, quality, numeric, categorical and schema information with accessible empty states.
- Utility manages reusable utility Python modules, including parsed syntax failure locations.
  Shared preview chrome supports resizing, collapse and keyboard-accessible tabs.

## Design rationale

Virtualisation and delegated cell events make tabular inspection remain responsive at large
dimensions. Cache identity excludes view-only Explore overview toggles so changing displayed
cards does not invalidate an otherwise reusable report. The UI also rejects stored jobs and
reports whose hash or source no longer matches; utility-module saves separately guard stale
responses after an awaited request.

## Interactions

It uses graph, settings, UI and node-results stores, the API client, and shared diagnostics/UI
components. Its cell-click callback feeds [frontend-trace-ui](../frontend-trace-ui/high-level.md).
Explore configuration is edited by [frontend-node-editors](../frontend-node-editors/high-level.md).

## Failure model

Preview errors are normal rendered states. Explore start failures and cancellation responses that
do not include a completed report are recorded as terminal errors and surfaced to the user; a
report/job with a stale hash or source is not rendered. Invalid optional overview configuration is
discarded while parsing, while malformed data that a renderer cannot safely interpret is allowed
to surface rather than being fabricated.

## Polars backend contracts (0.6.0)

See [the remediation plan](../../trip/plans/F_0.6.0_polars-backend-remediation.plan.md).
Preview and Explore surfaces will expose the five authoritative version-1 API states:
`projected`, `boundary`, `admitted_eager`, `rejected`, and `not_planned`. They also expose a
separate diagnostic-unavailable state when the shared parser rejects a missing/malformed payload,
an unknown version-1 enum, or an unsupported higher schema version. Unknown additive fields are
ignored only within version 1.

The status identifies blocking node/operator/profile, cost, and remediation where present; raw
bounded support/provenance detail remains available on demand. `detail_state` distinguishes
available, unavailable, and deterministically truncated detail. A group-by shown as `boundary`
must be the declared, RAM-admitted `materialisation-boundary`; a non-admitted group-by is a typed
HTTP 422 rejection, never ordinary or unprojected streaming execution. The UI never invents data,
treats `not_planned` or diagnostic unavailable as success, or treats a partial result as complete.
Stable contract-error codes and named fields remain available to accessible error copy.
