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
  an ordinary source's chip shows the sanitised node label, and a submodel `out__` edge's input name
  is the occurrence's name (or `<name>__<port_name>` with several output ports), resolved by the backend identity endpoint from the alias the request carries. Inside a
  drilled submodel, an edge from the composite Input resolves its row handle to that public
  input port's name; the literal boundary-card label
  `INPUT` is never presented as the child's argument name. The source
  node is named in the chip tooltip. Two frames connected from one API input render as two
  distinct, individually removable chips with two distinct names. Live-switch mapping rows and
  output frame blocks present the same names — there is no separate display identity anywhere.
- Editors retain incomplete persisted rows when they can be repaired (notably API schema and
  output mappings); fresh inference data may be normalised separately from persisted data.
- API Input preview browsing advertises and filters JSON, JSONL, NDJSON, and XML. Selecting any
  of those structured formats fetches its schema preview, and all four expose the cache/infer
  action. Directory rows remain navigable when the server reports a null size; only numeric file
  sizes are rendered.
- Banding exposes categorical/numeric rule editing, preview-derived suggestions and histogram
  context. Rating supports one- and two-way factor tables, value-level matching, statistics,
  paste/copy and downloadable table data.
- IO editors obtain supported formats and their arguments from the server. API/data input,
  output, external-file, transform, explore, live-switch, scenario, submodel,
  model-score and optimiser-apply editors render only their own configuration contract.
- The Explore Charts pane owns ordered version-1 PivotChart cards. `Add Chart` appends a complete
  enabled draft with a unique id/name; each card's checkbox changes only visibility, `Configure`
  never toggles it, and a separately confirmed Delete removes only that card. Back changes only
  navigation view state — it clears the node's stored configured-chart id and never touches card
  config or the preview pane.
- Chart Configure selects any pivot on the same Explore node, including a hidden one, by stable id.
  It is a chart-formatting surface only: pivot structure (fields, zones, filters) is edited
  exclusively in the Pivots editor, and the chart view renders no field well, field summary, or
  disclosure box between the source picker and the chart controls. It
  exposes the chart name, a chart-type
  gallery over four options — Combo leftmost as the general category and the default for a
  newly sourced multi-Value chart (as in Excel; a single-Value chart's plain-column seed reads
  as Clustered columns), then clustered, stacked, and 100% stacked columns —
  where exactly one option is always highlighted: any
  arrangement beyond the three column layouts reads as Combo and is refined per Value through
  its chart-type and
  axis selects, plus a vertical/horizontal orientation toggle preserved
  across preset
  application, per-Value defaults with exact-series overrides nested beneath each Value box (a
  collapsed expandable list, present only when a Columns field splits that Value into several
  series or overrides already exist — a single-series Value's box is its series config), two
  numeric axes presented as separate Primary and Secondary boxes ordered before the per-Value
  boxes, with the Secondary box gated by a "Use secondary axis" checkbox whose untick moves
  secondary-assigned series back to primary in one edit, a Legend box after the Secondary box
  gated by a "Show legend" checkbox, and
  category-label controls. Navigation alignment is preview-driven — selecting Pivots or Charts
  in the lower preview aligns the editor, while editor-side selections and Configure/Back
  never change the preview; the configured subview is per-node view state that survives pane
  switches and clears when its card is deleted. Per-series controls use user-facing vocabulary — chart type, series,
  stacking (None / Stacked / 100% stacked with valid-by-construction group transitions), and a
  swatch-based colour control with an Automatic reset — and unused formatting is described by
  the series or Value name it belonged to, never by an internal id. A pivot Value the chart
  does not yet encode is reconciled with seeded
  defaults (surfaced as such, persisted with the next committed edit) instead of blocking the
  editor. Changing an already mapped source requires confirmation and commits
  the reset in one graph edit. Draft, missing, unconfigured, loading, stale, errored, hidden, and
  ready source states remain explicit, but the source picker itself never carries a status
  suffix — options show the pivot name (plus a hidden marker where applicable), and source
  state is communicated by the status messaging in the Configure body.
- A pivot cannot be deleted while charts reference it; the Pivots pane identifies dependent chart
  names so the analyst can reassign them. Chart appearance edits are presentation-only and never
  change dataframe/pivot calculation identities or structural execution version.
- Overview, Pivot, and Chart cards share one Explore toggle-card presentation. The card body is an
  accessible checkbox target: enabled cards use the Explore border, accent-soft background, and
  accent label treatment; disabled cards use the neutral input treatment. Pivot and Chart cards
  keep Delete and Configure as separate controls that never toggle visibility. Their list headers,
  Add actions, empty states, optional detail text, and delete eligibility remain supplied by the
  owning panes.
- The Explore Pivots pane owns ordered version-1 cards. `Add Pivot` appends a fully populated,
  uniquely named, enabled card with the first-unused `pivot_N` identity. Each card exposes an
  accessible visibility checkbox and a separate `Configure` button; configuring never toggles
  visibility, and Back returns to the card list by clearing the node's stored configured-pivot
  id without touching card config or the preview pane.
- Pivot Configure provides a committed unique name, an Excel-style searchable dtype-labelled
  field palette in a fixed-height scrolling list, and ordered Filters, Columns, Rows, and Values
  zones. A current Explore cache report supplies the palette's authoritative post-analysis schema;
  before one is available the editor may use the upstream preview schema, but it never uses a
  retained report from a different graph/source identity. Every field row shows `Add to:` followed
  by Filters, Columns, Rows, and Values buttons;
  pressing one adds that field directly to the matching zone, with no separate selection/action
  area. Assigned fields appear beneath `Drag fields between areas below:` in a fixed two-column
  grid ordered Filters, Columns, Rows, Values like Excel. A placement can be dragged onto another
  placement to insert before it, or onto open area space to append; this reorders within an area or
  moves between valid areas in one committed graph edit. The visible Move-to and Move-up/down
  controls are omitted; focused cards expose equivalent arrow-key movement, and Remove remains a
  separate action. One field may appear across zones; Filters/Columns/Rows
  reject same-zone duplicates; Values permit repeated fields with stable ids. Numeric Values
  default to Sum and other dtypes to Count, with Sum/Count/Average/Min/Max/Median/Distinct count
  available subject to dtype compatibility. Placement cards contain only placement-specific
  controls (filter members, Value aggregation, and Remove); sorting and formatting never appear
  inside the draggable grid. The Configure subview starts directly with the Pivot name setting,
  without repeating `Configure <name>` above it. Sorting, Formatting, and Conditional Formatting
  use the standard uppercase node-editor micro-title, standalone above their settings and never
  inside a bordered settings box. Immediately after the grid, the Sorting settings box exposes
  `Sort by` (default Row-label order, any placed Row, or any placed Value) and its matching `Order`
  control side by side. A following Formatting section lists every
  displayed Column, Row, Value, and selected-formula placement, with Values and formulas numbered
  independently per kind. Numeric output can use General, Number, Percentage,
  GBP currency, USD currency, or EUR currency formatting, Automatic or a fixed 0–10 decimal
  places, and an explicit thousands-separator option. Filters are omitted because they do not
  render in the pivot table; non-numeric placements remain identified but have no numeric-format
  controls, without redundant introductory copy above the placement list. Formatting is
  presentation-only and updates a retained result immediately. A separate
  Conditional Formatting title is followed by a bordered rules box. Every active rule is visible
  at the same time and exposes its Value field, colour scale, an optional `Split scale by`
  selector, labelled gradient preview, and Remove action; the `Add rule` action follows the rule
  list at the bottom of the box. Split choices are restricted to fields currently placed in Rows
  or Columns. With no split, one scale covers the whole Value; selecting a placement gives each
  distinct typed member of that Row or Column field its own scale. The preview uses Excel's prominent
  red–yellow–green three-colour palette (`#F8696B`, `#FFEB84`, `#63BE7B`), reversing the same
  endpoints for green-to-red. One Value placement can have at most
  one rule. Adding chooses the first still-unformatted numeric Value and applies `Low red → High
  green`; the action is disabled when no eligible Value remains, without an additional
  all-fields-configured message. A rule can be reassigned to any
  other eligible unformatted Value. Colour choices are `Low red → High green` and `Low green →
  High red`; removing a rule persists its scale as None and clears its split. Reassigning a rule
  carries its split with it. Removing the selected Row/Column placement, or moving it out of those
  two zones, clears every rule that referenced it; moving it between Rows and Columns preserves the
  stable reference. An aggregation change that makes a Value non-numeric also removes its rule and
  split. Missing source fields remain visible as invalid chips.
- Configuration edits commit immediately as ordinary graph changes. When the lower Pivots or
  Charts result pane is mounted, a committed calculation-affecting Pivot edit automatically
  schedules one recalculation for the current dataframe-cache and calculation identities;
  opening either pane later does the same for stale or missing source results. Pivot and Chart
  name/appearance edits reuse retained data and rerender immediately without calculation. There
  is no separate `Update preview` or routine manual refresh step.
- The Explore node pane strip is ordered Polars Code, Overview, Pivots, Charts, Export.
  Relationships is not exposed as an Explore pane. Pivots hosts its card workflow in that
  position, and its selection is remembered independently per Explore node like the other panes.
- The Edge Join editor presents the canvas-bound dominant/base and joining roles as fixed
  connections with one atomic swap action. Each role displays the executable input name
  contributed by that exact edge, using the same identity rule as other node editors: an API
  Input edge displays its selected frame label, while an ordinary edge displays the upstream
  node's executable input name. Internal source-node identities are not exposed in the role text
  or its truncation tooltip. Swapping updates only the incoming role handles in one graph
  transaction. Join type choices are exactly `inner`, `left`,
  `right`, `full`, `semi`, `anti`, and `cross`. A cross join has no key controls or persisted
  keys; every other mode requires either one-or-more same-name `on` keys or equal-length,
  non-empty `leftOn`/`rightOn` pairs, and the two key forms cannot coexist.
- Renaming an ordinary source or an API-input frame atomically migrates downstream
  `input_scenario_map`, instance `inputMapping`, and exact-name Optimiser/Optimiser Apply input
  selectors. A duplicate post-rename input
  name rejects the entire edit and is shown inline; no graph or mapping change is partially
  applied.
- Data Input and Data Output obtain a fresh capability payload when an editor mounts; mounts
  sharing the same pending request coalesce it. Provider changes replace the discriminated
  config in one undoable update, and output overwrite confirmation is tied to semantic graph
  and execution settings rather than preview/trace metadata.
- Data Input groups providers as File, Database, Lakehouse, Databricks, and
  Inline and derives every supported field, format, mode, dependency,
  snapshot build class, and cache control from the backend capability
  contract. A single available read mode is not rendered. Cache mode is also
  not presented as a choice: file-backed Parquet scans directly and has no
  cache action; every other input uses the shared Cache-as-Parquet control.
  Its optional Polars editor transforms the resolved frame.
  Data Output presents only writable groups/modes, never Databricks or a Polars
  editor, resolves the actual destination, and keeps per-node write,
  collision-confirmation, and terminal state across panel remounts. Inactive
  discriminated-branch keys are removed rather than preserved invisibly.
- Rating consumes healthy configured Banding outputs across continuous,
  categorical, and breakpoint shapes. Recognised non-blank outputs with zero
  valid levels produce one accessible warning and cannot be silently refilled
  from stale preview/table levels; healthy factors remain usable. Rebuilding
  several factors constructs their full Cartesian table, and edited
  relativities survive save/reload.
- The maintained Banding-to-Rating configuration-shape matrix names one
  component owner, representative fixture, and smallest proving test tier for
  continuous, categorical, breakpoint, mixed-factor, zero-level, malformed,
  mixed-output, and persisted-table variants. Browser promotion is reserved
  for cross-editor persistence/keyboard journeys rather than duplicating every
  component shape.
- Rating, Output, and API Input expose only their current persisted shapes:
  Rating uses `tables[].entries` plus `combinedOutputs`; Output uses
  `outputMapping` rows with all four required fields including `enabled`; API
  Input uses `tables`. Editors do not detect, upgrade, or mirror historical
  working-copy formats.

## Design rationale

The UI uses specialised editors rather than one schema-driven form because graph node contracts
are structurally different. Shared helpers centralise the places where consistency matters:
commit timing, clipboard parsing, path handling, rendered input-source chips, and normalisation
of persisted banding/rating data. Rating normalisation preserves optional factor-dtype
descriptors and ordered entry rows so opening and saving a table cannot erase backend-owned
lookup identity. Lazy dispatch keeps editor code out of the initial canvas load.
Accessibility automation is deliberately risk-based: component tests enforce
roles, names, descriptions, invalid state, and focus behaviour, while the
stable cross-editor Playwright journey enforces keyboard completion and
reviewed desktop/narrow visuals. Haute does not currently run a blanket DOM
scanner or claim whole-application WCAG conformance.

Display identity and executable identity are one identity. `InputSource.name` is the input's
single name — the chip text, the code argument, and the key persisted contracts use (the
live-switch `input_scenario_map` and the instance `inputMapping`, both consumed by the
backend). It is read per edge by the shared `edgeInputName` helper from server-owned editor
identity metadata, so the panel can never advertise a name the code does not
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
to the parent node and never a normal-looking entry. WebSocket-synchronised graphs can retain a
null-handle API-input edge so the user can repair the source file without silently losing
topology. Such an edge is displayed with the explicit `<unresolved>` marker and warning state;
it never crashes the panel or aliases the API input's sole emitted table.
An Edge Join with missing/ambiguous role edges, an unknown join mode, or invalid key shape remains
visibly invalid and blocks save; the editor never infers a role or silently substitutes join
keys. Edge Join diagnostics use the danger/error treatment rather than warning colours. Empty or
otherwise invalid visible join-key controls expose `aria-invalid` and a red border; when a
non-cross join has no keys, this applies to the required control or controls in the active key
mode. The incoming edges' `base`/`join` target handles are the only persisted role authority;
the removed `baseInput`/`joinInput` config representation has no compatibility path.

## Recovery diagnostics in node presentation

Editor-load availability is separate from transient execution status. A known recovered node uses
its normal canvas card with an accessible `unavailable` or `blocked` load indicator; an unknown or
removed authored decorator uses a dedicated recovery-only card that is not present in the palette
and cannot serialize as a canonical node. Unavailable cards retain authored identity and decorator
spelling instead of coercing the node to a supported type.

Selecting an unavailable or blocked node opens a read-only diagnostic inspector rather than its
normal configuration editor. The inspector shows attributed messages, remediation, source/config
location, incident id, and blocking path where present. Ready siblings in a degraded document may
be inspected through a static read-only configuration view. Normal editors are not mounted in
that state, so editor effects cannot start schema, preview, training, cache, or publication work
behind disabled controls. Selection, panning, zooming, recovery preview, and diagnostic inspection
remain available.

## Minimal unavailable-node removal

An unavailable node inspector may offer `Remove node` only when the validated
document capability allows repair and the node has a server recovery identity.
The action first opens a dry-run confirmation surface; it never invokes normal
node deletion, graph Save, codegen, or a client-authored source rewrite. The
surface lists every file that will change or be deleted, renders the bounded
server patch, states that the referenced config is retained by default, and
requires a separate explicit choice before config deletion is added to the
plan. Changing that choice obtains a new plan and plan hash.

Confirmation applies exactly the displayed plan hash. A revision conflict,
implicit downstream consumer, ambiguous identity/span, mixed connection
chain, shared config, or server verification failure stays visible and leaves
the recovery inspector open. Success adopts the returned editor document and
closes the removed node's panel. Blocked and ready nodes never expose this
action. No `Upgrade node` or migration action is rendered.
