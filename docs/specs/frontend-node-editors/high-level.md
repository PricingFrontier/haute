# Frontend Node Editors — High-Level Specification

## Purpose

The node-editor surface turns the selected pipeline node into a type-specific, editable
configuration form. It keeps authoring consistent across simple scalar nodes, code nodes,
multi-frame API inputs, IO sources/sinks, banding, rating, model scoring, submodels, and
optimiser application.

## Scope

This component owns node-panel dispatch, the node palette, editor implementations and their
form, banding, rating, path, clipboard, and format helpers. It also owns the generic Columns
and grouped-columns configuration tabs. The graph canvas owns selection and graph mutation;
backend API modules own validation and persistence.

## Behaviour

- Selecting a node opens its matching editor through a lazy editor registry. An unknown type,
  malformed instance reference, or unsupported read-only configuration is shown as diagnostics
  instead of being guessed.
- Editor updates flow through the supplied configuration callbacks. Text and numeric drafts are
  committed on blur/Enter where their controls use the shared committed-input primitives, so a
  normal edit does not create a graph mutation for each keystroke.
- Connected inputs are listed by their **input name — the exact argument name in the node's
  code**, 1:1 with the generated function signature: an API-input frame edge's chip shows the
  frame label carried on the edge (`quotes` is displayed as `quotes` and callable as `quotes`),
  an ordinary source's chip shows the sanitised node label, and a submodel-output edge's chip
  shows the child node's sanitised label (what the flattened code actually binds). The source
  node is named in the chip tooltip. Two frames connected from one API input render as two
  distinct, individually removable chips with two distinct names. Live-switch mapping rows and
  output frame blocks present the same names — there is no separate display identity anywhere.
- Editors retain incomplete persisted rows when they can be repaired (notably API schema and
  output mappings); fresh inference data may be normalised separately from persisted data.
- Banding exposes categorical/numeric rule editing, preview-derived suggestions and histogram
  context. Rating supports one- and two-way factor tables, value-level matching, statistics,
  paste/copy and downloadable table data.
- IO editors obtain supported formats and their arguments from the server. API/data input,
  output, source, sink, external-file, transform, explore, live-switch, scenario, submodel,
  model-score and optimiser-apply editors render only their own configuration contract.
- The Edge Join editor presents the canvas-bound dominant/base and joining roles as fixed
  connections with one atomic swap action. Swapping updates the incoming role handles and
  `baseInput`/`joinInput` config together. Join type choices are exactly `inner`, `left`,
  `right`, `full`, `semi`, `anti`, and `cross`. A cross join has no key controls or persisted
  keys; every other mode requires either one-or-more same-name `on` keys or equal-length,
  non-empty `leftOn`/`rightOn` pairs, and the two key forms cannot coexist.

## Design rationale

The UI uses specialised editors rather than one schema-driven form because graph node contracts
are structurally different. Shared helpers centralise the places where consistency matters:
commit timing, clipboard parsing, path handling, rendered input-source chips, and normalisation
of persisted banding/rating data. Rating normalisation preserves optional factor-dtype
descriptors and ordered entry rows so opening and saving a table cannot erase backend-owned
lookup identity. Lazy dispatch keeps editor code out of the initial canvas load.

Display identity and executable identity are one identity. `InputSource.name` is the input's
single name — the chip text, the code argument, and the key persisted contracts use (the
live-switch `input_scenario_map` and the instance `inputMapping`, both consumed by the
backend). It is derived per edge by the shared `edgeInputName` helper, mirroring the backend's
`edge_input_name` byte-for-byte, so the panel can never advertise a name the code does not
recognise. (An earlier interim design split `varName` from `displayLabel` to avoid touching
executable contracts in a presentation-only release; that split itself produced the
name-shown-but-not-callable confusion and was deliberately retired by the convergence release.)

## Interactions

The panel consumes selected-node/edge state from
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md), API capabilities from
[server-api](../server-api/high-level.md), and modelling/optimiser configuration panels from
[frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/high-level.md). Preview
columns and rows are supplied by the execution/result stores, not computed by these editors.
Frame display labels for input chips and output frame naming are resolved through the
api-input frame-identity helpers owned by
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)
(`frontend/src/utils/apiInputPorts.ts`).

## Failure model

Client-side parse and shape checks show inline invalid state where implemented. Server failures
such as format, file, Databricks, or MLflow lookup errors are rendered by the invoking editor.
Malformed config that cannot be interpreted is surfaced as a visible diagnostic or an explicit
editor error; the component does not silently replace it with invented configuration. A dangling
`sourceHandle` (an edge bound to a frame that no longer exists) is displayed **verbatim** as the
edge's frame identity with an explicit unresolved warning state wherever the connection is
presented (input chips, live-switch mapping rows, output frame blocks) — never silently renamed
to the parent node and never a normal-looking entry. Null-handle API-input edges cannot be
created (the zero-frame handle is non-connectable) and are pruned by reconciliation when read
from a hand-edited file, so the verbatim-plus-warning rule is the complete unresolved story.
An Edge Join with missing/ambiguous role edges, conflicting stored roles, an unknown join mode,
or invalid key shape remains visibly invalid and blocks save; the editor never infers a role or
silently substitutes join keys.

## Approved change contract — 0.7.0 unified data I/O editors

Remaining node-editor improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md)
and the approved [I/O behaviour](../io-layer/high-level.md#approved-change-contract-070-data-io-convergence).

- The palette and editor registry expose one **Data Input** and one **Data Output** node type.
  Data Source and Data Sink entries/editors are deleted with no legacy rendering path. Multiple
  input/output instances remain allowed on a graph.
- Data Input begins with an input-type menu: **File**, **Database**, **Lakehouse**,
  **Databricks**, and **Inline**. Registry-backed sections show only their backend-declared
  formats, modes, fields, arguments, dependencies, direct-batching capability, and snapshot
  capability. Databricks keeps its dedicated warehouse/catalog/schema/table controls and shared
  snapshot controls; it does not show a meaningless Polars format/mode selector.
- The common snapshot panel shows direct/snapshot choice where both are valid and
  Build/Refresh, progress, readiness, freshness, generation metadata, and Clear where snapshots
  are supported. Mandatory-snapshot providers do not pretend direct mode is selectable.
  Readiness and freshness have distinct labels, and `unknown` freshness is not styled as success.
- The optional Polars editor appears after every Data Input provider section, with wording that
  it transforms the opened direct source or cached snapshot. Chunk-incompatible code produces
  the execution planner's actionable diagnostic; the editor never promises that arbitrary code
  is chunk-local.
- Switching input type is one undoable graph mutation which replaces the discriminated config
  and removes inactive keys. There is no hidden reuse of a path, URI, query, table, records, or
  arguments from another branch.
- Data Output mirrors the input groups and format language but filters by write capability.
  It shows destination, supported sink/write mode, arguments, dependency and boundedness
  diagnostics, overwrite/append/replace/upsert options only when declared, and the explicit
  **Write** button with progress/result status retained from Data Sink. It has no Polars editor.
  Databricks is not displayed as an output group in 0.7.0.
- A format with a missing optional engine remains visible with an actionable dependency warning;
  a format with no capability for that direction is not presented as working. The UI never
  hard-codes format membership or silently substitutes a default when capability loading fails.

Acceptance includes component tests for every group/config transition, capability-driven option
sets, mandatory/optional/no-cache states, progress/freshness/error states, Polars-editor
placement, inactive-key removal and undo, output Write gating/status, and unavailable engines.
Browser coverage creates, configures, saves, reloads, executes, snapshots, and writes the
retained node types and asserts that no Data Source/Data Sink palette/editor affordance exists.

## Approved change contract — Banding-to-Rating canvas assurance

This contract implements ROAD-UI-01, ROAD-UI-02, ROAD-UI-03 and the node-editor portion of
ROAD-UI-04 in the [frontend canvas roadmap](../../roadmap/frontend-canvas.md).

- **Current limitation.** Continuous, categorical, and breakpoint Banding shapes have focused
  helper tests, but there is no owned cross-shape assurance matrix or deterministic browser
  journey into a persisted Rating table. A configured Banding output with no valid levels can
  silently disappear from downstream Rating choices.
- **Target behaviour.** One explicit matrix assigns continuous, categorical, breakpoint, mixed,
  zero-level, malformed/partial, and persisted-table shapes to named fixtures, test owners, and
  tiers. Rating discovery accepts all healthy configured Banding outputs, rebuilding three named
  factors creates their complete Cartesian table, an edited relativity survives save/reload, and
  malformed inputs never crash the panel. Once a recognised factor has a non-blank output name,
  zero valid levels produce one accessible aggregated warning naming the affected outputs while
  healthy choices remain usable.
- **Warning boundary.** No warning is shown merely because the graph has no Banding node, a
  Banding node is still an unnamed draft, a factor output is blank, or every configured output is
  healthy. A loaded factor with a recognised mode and non-blank output is configured even when
  its rules are empty or malformed, so that state warns instead of silently reusing stale raw or
  saved levels for that output.
- **Non-goals and compatibility.** The change does not alter Banding execution semantics,
  relativity mathematics, table JSON shape, or existing raw/saved-level fallback for columns that
  are not claimed by configured Banding factors. Existing one-factor and two-factor Rating tables
  remain editable.
- **Acceptance.** Unit/component tests pin every matrix row and warning boundary. The
  deterministic browser journey proves three named factors, all Cartesian entries, a keyboard
  rebuild/edit/save path, and the edited value after reload. Stable screenshots cover the mixed
  Banding editor and rebuilt Rating state at desktop and the supported narrow viewport.

## I/O authoring feedback and output lifecycle

The retained editors implement the editor portion of
[IO-IO01, IO-IO06, IO-IO08, and IO-IO09](../../roadmap/io-layer.md).

- A file Data Input whose backend capability requires a bounded schema shows
  schema-fetch progress, a preview, and any safe route diagnostic inline.
  **Use detected schema** merges the detected ordered dtype map into
  `arguments.schema` without discarding delimiter or other arguments. A
  visible warning remains until a schema mapping is present.
- API Input also renders the shared schema-fetch error instead of discarding
  it. Starting another fetch clears the old error, so recovery is visible.
  Its retained JSON-family picker exposes `.json`, `.jsonl`, and `.ndjson`;
  cancelling/closing the picker does not mutate config.
- Data Output renders its resolved destination before writing and warns when
  an explicit file extension disagrees with the selected capability.
  While an HTTP write is pending it renders an indeterminate progress status
  and disables another write for that node.
- Output write state is stored per node outside the editor component. Switching
  panels cannot forget a pending request, enable a duplicate request, or lose
  the last success/failure. Completion for an obsolete config identity is not
  presented as the current config's result.
- A 409 collision renders an explicit **Replace existing file** action. Only
  that action retries with `overwrite=true`; ordinary Write sends false.
  Success uses structured `row_count` and `path`, and API failures prefer the
  safe server `detail` over a generic HTTP status string.

Component tests cover schema merge/preservation, loading/error/recovery,
destination preview/mismatch, remount during a pending write, structured
success/failure, and the overwrite confirmation retry.
