# Frontend Node Editors — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/NodePanel.tsx` | Selects configuration/columns/instance views, routes the selected node to an editor, hosts the supported modelling node's five-pane strip and active-training indicator, and derives the per-edge `InputSource` list via `edgeInputName` (memoised on a per-edge signature covering edge id, source id/label, `sourceHandle`, the derived input name, and the `frameUnresolved` resolution state). |
| `frontend/src/panels/PreviewPanelTabs.tsx` | Generic ARIA tab strip owned by [frontend-preview-explore](../frontend-preview-explore/low-level.md) and used by the node panel for the five modelling panes and active-training indicator. |
| `frontend/src/panels/NodePalette.tsx` | Renders draggable node templates. |
| `frontend/src/panels/LazyNodeEditors.tsx` | Central dynamic-import registry and loading boundaries for editor bodies. |
| `frontend/src/panels/PanelShell.tsx`, `frontend/src/panels/PanelHeader.tsx` | Right-panel shell/header used by node, imports and utility authoring views. A shell with no stored width chooses 50% of the available space when it mounts and keeps that established width across unrelated rerenders and viewport changes; an explicit drag updates the shared stored width. |
| `frontend/src/components/ReadOnlyNodeConfig.tsx`, `frontend/src/components/FramesTable.tsx`, `frontend/src/components/KeyPickerModal.tsx` | Inert configuration, API-frame rows and reusable API-input key picker. |
| `frontend/src/panels/editors/index.ts` | Public editor exports. |
| `frontend/src/panels/editors/_shared.tsx` | Shared editor types, styles, file browser (nullable directory size, numeric file-size rendering), schema preview and the input-source bar (chips keyed by edge id, showing each edge's input name — the code argument — with the source node named in the tooltip). |
| `frontend/src/components/ColumnTable.tsx`, `frontend/src/components/CacheFetchButton.tsx` | Reusable column-selection table and API-input cache action/status control. |
| `frontend/src/utils/dataInputMode.ts` | Shared `dataInputIsDirect` derivation mirroring the backend's `data_input_is_direct`; drives the Data Input editor's cache surface and [frontend-graph-canvas](../frontend-graph-canvas/low-level.md)'s `ensureInputSnapshots` orchestration. |
| `frontend/src/panels/editors/CodeEditor.tsx`, `frontend/src/panels/editors/CodeMirrorEditor.tsx`, `frontend/src/panels/editors/shared/PolarsCodePanel.tsx`, `frontend/src/panels/editors/shared/PathPickerField.tsx` | Code-editor wrappers, Polars-specific panel, and the shared selected-path picker. |
| `frontend/src/panels/editors/ConstantEditor.tsx`, `frontend/src/panels/editors/TransformEditor.tsx`, `frontend/src/panels/editors/EdgeJoinEditor.tsx`, `frontend/src/panels/editors/LiveSwitchEditor.tsx`, `frontend/src/panels/editors/ScenarioExpanderEditor.tsx` | Editors for scalar, transform, join, conditional-switch and scenario nodes. `EdgeJoinEditor` exposes fixed canvas-derived base/join roles, atomic swap, the seven supported join modes, mutually exclusive same-name/asymmetric key forms, and advanced Polars options. |
| `frontend/src/panels/editors/ExternalFileEditor.tsx`, `frontend/src/panels/editors/DataInputEditor.tsx`, `frontend/src/panels/editors/DataOutputEditor.tsx` | External-object, grouped tabular input, and grouped tabular output configuration. |
| `frontend/src/stores/useOutputWriteStore.ts` | Per-node output-write request identity, pending/terminal lifecycle, and overwrite-confirmation state retained across editor remounts. |
| `frontend/src/panels/editors/_IoFormatEditor.tsx`, `frontend/src/panels/editors/_ioFormats.ts`, `frontend/src/panels/editors/_DatabricksSelector.tsx`, `frontend/src/panels/editors/_InputSnapshotCacheButton.tsx` | Registry-driven IO arguments, mount-refetched capabilities with concurrent-request coalescing, dedicated Databricks browsing, and the shared-button input-snapshot lifecycle. |
| `frontend/src/panels/editors/ApiInputEditor.tsx`, `frontend/src/panels/editors/apiInputSchema.ts`, `frontend/src/panels/editors/apiInputInherit.ts`, `frontend/src/panels/editors/FrameTableActions.tsx` | API-input frame/schema editing, JSON/JSONL/NDJSON/XML preview selection and cache action, persisted/inferred schema conversion, reconciliation and row actions. |
| `frontend/src/panels/editors/OutputEditor.tsx`, `frontend/src/panels/editors/outputMappingSchema.ts`, `frontend/src/panels/editors/outputPathTools.ts`, `frontend/src/panels/editors/jsonpath.ts`, `frontend/src/panels/editors/JsonPreview.tsx` | Output mappings, JSON-path validation/rewrites and preview. |
| `frontend/src/panels/editors/ColumnsTab.tsx` | Generic column selection and rename configuration. |
| `frontend/src/panels/editors/ExploreCodeEditor.tsx`, `frontend/src/panels/editors/ExploreOverviewConfig.tsx`, `frontend/src/panels/editors/ExplorePivotsConfig.tsx`, `frontend/src/panels/editors/ExploreChartsConfig.tsx` | Explore-code, overview-card, pivot-card, and chart-card configuration. The Pivots and Charts editors own their list/configure navigation; chart parsing and identity allocation are also shared with the visualisation pane. |
| `frontend/src/panels/editors/explorePivots/placements.ts` | Pure pivot placement domain helpers shared by the pivot editor and its subviews: zone types and labels, placement add/remove/append transforms, sort-ordering normalisation, duplicate-field checks, and typed member identity. |
| `frontend/src/panels/editors/explorePivots/FilterMemberPicker.tsx` | Filter-member picker subview: immediate initial load, debounced non-empty search, request aborting, and Explore-cache-identity gating of displayed members. |
| `frontend/src/panels/editors/explorePivots/ZoneSection.tsx` | One drag-and-drop area-grid zone: placement chips, keyboard repositioning, aggregation selection, remove actions, and the nested filter-member picker. |
| `frontend/src/panels/editors/explorePivots/PivotFieldWell.tsx` | Pivot field-authoring surface composed by the Pivots editor: field search, dtype-labelled available-fields list with per-zone Add actions, the four-zone `ZoneSection` grid, and pointer/keyboard placement state. Props: the pivot, `persistPivot`, upstream columns, filter-member loading, and the current config hash. |
| `frontend/src/panels/editors/ExploreToggleCard.tsx` | Shared full-body Explore checkbox card used by Overview, Pivot, and Chart configuration, including enabled/disabled presentation and accessible label/description wiring. |
| `frontend/src/panels/editors/ExploreConfigCardList.tsx` | Shared Pivot/Chart list header, empty state, and action-card row, composing `ExploreToggleCard` with separate Delete and Configure actions. |
| `frontend/src/panels/explore/chartConfig.ts` | [frontend-preview-explore](../frontend-preview-explore/low-level.md)-owned chart version-1 validation and identity helpers consumed by the chart editor. |
| `frontend/src/panels/explore/pivotConfig.ts` | [frontend-preview-explore](../frontend-preview-explore/low-level.md)-owned pivot validation and identity helpers consumed by the pivot editor, including allocation of the first unused pivot id. |
| `frontend/src/panels/editors/MlflowModelPicker.tsx`, `frontend/src/panels/editors/ModelScoreEditor.tsx`, `frontend/src/panels/editors/OptimiserApplyEditor.tsx`, `frontend/src/panels/editors/SubmodelEditor.tsx` | MLflow/model-score, optimiser-apply and submodel editors. |
| `frontend/src/panels/editors/BandingEditor.tsx` | Composes banding mode, rules, histogram and generation controls. |
| `frontend/src/stores/useNodeResultsStore.ts`, `frontend/src/stores/useUIStore.ts` | [frontend-shared](../frontend-shared/low-level.md)-owned active-job state and per-node pane memory consumed by node-panel modelling chrome. |
| `frontend/src/utils/trainingObjective.ts` | Click-time training issue aggregation owned and consumed by [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/low-level.md). |
| `frontend/src/panels/editors/banding/index.ts`, `frontend/src/panels/editors/banding/bandingUtils.ts` | Banding public barrel and rule/level utility functions. |
| `frontend/src/panels/editors/banding/BreakpointGrid.tsx`, `frontend/src/panels/editors/banding/BandingRulesGrid.tsx`, `frontend/src/panels/editors/banding/CategoricalValuePicker.tsx` | Numeric breakpoints, editable rules and categorical selection. |
| `frontend/src/panels/editors/banding/BandingHistogram.tsx`, `frontend/src/panels/editors/banding/GenerateBandsDialog.tsx` | Histogram context and generated-band dialog. |
| `frontend/src/panels/editors/RatingStepEditor.tsx` | Rating-table and combined-output orchestration. |
| `frontend/src/panels/editors/rating/index.ts`, `frontend/src/panels/editors/rating/ratingTableUtils.ts`, `frontend/src/panels/editors/rating/cellStyles.ts` | Rating barrel, normalisation/levels/statistics/colours and cell styles. |
| `frontend/src/panels/editors/rating/OneWayEditor.tsx`, `frontend/src/panels/editors/rating/TwoWayGrid.tsx`, `frontend/src/panels/editors/rating/ControlledNumberCell.tsx`, `frontend/src/panels/editors/rating/StatsFooter.tsx` | One-/two-way editing, commit-on-blur number input and table statistics. |
| `frontend/src/panels/editors/shared/tableClipboard.ts` | Clipboard parsing/writing and TSV/CSV download helpers shared by editable grids. |
| `frontend/src/utils/buildGraph.ts` | Cross-component dependency owned by [frontend-graph-canvas](../frontend-graph-canvas/low-level.md); Data Output consumes the canonical graph payload and request-identity projection. |
| `frontend/src/utils/configField.ts`, `frontend/src/utils/banding.ts` | Typed config readers and Banding classification owned by [frontend-modelling-optimiser-ui](../frontend-modelling-optimiser-ui/low-level.md) and consumed by node editors. |
| `frontend/src/components/form/index.ts`, `frontend/src/components/form/CommittedTextField.tsx`, `frontend/src/components/form/ConfigCheckbox.tsx`, `frontend/src/components/form/EditorLabel.tsx` | Form barrel, committed text/area drafts, config checkboxes, and accessible editor labels owned by [frontend-shared](../frontend-shared/low-level.md). |

## Key types and data structures

- `OnUpdateConfig`, `OnReplaceConfig`, `SimpleNode`, `SimpleEdge`, `SchemaInfo` and `InputSource` in
  `frontend/src/panels/editors/_shared.tsx` define the editor-to-panel contract.
  `OnUpdateConfig` returns a commit result — `{ ok: true } | { ok: false; error: string }` —
  from `App.onUpdateNode`'s preflight: editors whose edits can trigger a frame rename (the
  ApiInputEditor label commit) surface `error` inline beside the offending field and clear it
  on the next successful commit; edits that cannot fail preflight may ignore the return value
  unchanged. Every production supplier of the callback migrates with the type — including the
  read-only inspector (`frontend/src/components/ReadOnlyNodeConfig.tsx`), whose inert no-op
  callback returns `{ ok: true }` — so the contract change is compile-time loud, not
  runtime-discovered. `InputSource`
  carries one identity: `name` is the input's single name — chip text, code argument, and the
  key persisted contracts use (the live-switch `input_scenario_map`, the instance
  `inputMapping`) — derived per edge by `edgeInputName` (an API-input edge's frame label
  verbatim; a submodel `out__` edge's child sanitised label; else the sanitised source-node
  label). `sourceLabel` is the raw source-node label used in tooltips and removal titles;
  `edgeId` is the stable chip key and removal target; `frameUnresolved` marks an API-input edge
  whose frame could not be resolved, rendering the chip in its warning state. `name` is
  required, so every fixture constructing an `InputSource` fails to compile until it declares
  one — the former `varName`/`displayLabel` pair no longer exists.
  `OnReplaceConfig` accepts a complete next config and returns the same commit result; provider
  switches use it to remove inactive branch keys in one undoable mutation.
- API schemas have separate persisted read/write and inferred/reconciled representations in
  `frontend/src/panels/editors/apiInputSchema.ts` and `frontend/src/panels/editors/apiInputInherit.ts`.
  Output mappings use the equivalent conversion boundary in
  `frontend/src/panels/editors/outputMappingSchema.ts`.
- `RatingTable` and factor/entry structures are normalised by
  `frontend/src/panels/editors/rating/ratingTableUtils.ts`. `RatingTable.factorDtypes` is an
  optional factor-name → backend dtype-descriptor map that is preserved for selected factors.
  The editor does not invent a descriptor when the backend has not supplied one.
  Banding grids consume and update the node's banding-rule records through
  `frontend/src/panels/editors/banding/bandingUtils.ts`.

## Control flow

1. `frontend/src/panels/NodePanel.tsx` receives selection and graph context, chooses an editor
   or generic tab, and passes config mutation callbacks and available preview/connection data.
   For each upstream edge it builds an `InputSource`: `name` via
   `edgeInputName(edge, sourceNode, submodels)` (from frontend-graph-canvas's
   `frontend/src/utils/apiInputPorts.ts`, mirroring the backend's `edge_input_name`). An
   API-input edge whose `sourceHandle` is null/undefined uses the explicit `<unresolved>` marker.
   A non-null handle naming no currently eligible frame keeps that handle **verbatim** as `name`.
   Both cases set `frameUnresolved: true`, so the chip shows the unresolved identity with its warning state
   instead of impersonating a resolved input or renaming it to the parent. The memo's
   staleness signature covers edge id, source id, source label, `sourceHandle`, the derived
   name, and the resolution state — so a frame rename (which rebinds the edge's
   `sourceHandle`) refreshes downstream chips, and a frame becoming resolvable under an
   unchanged name string clears the warning.
2. `frontend/src/panels/LazyNodeEditors.tsx` loads the selected editor module. React suspense
   displays its loading boundary while that import is unresolved; already-loaded modules are
   reused by the module loader.
3. Form controls keep an input draft locally and commit via blur/Enter where using
   `frontend/src/components/form/CommittedTextField.tsx`; grid components apply the same
   one-gesture boundary in their own controlled cells.
4. Schema, mapping, banding and rating editors derive visible rows from persisted config, accept
   user changes, normalise only at their documented conversion/update boundary, then invoke the
   panel callback. Rating factor changes filter `factorDtypes` atomically with `factors` and
   `entries`; removing a factor removes its descriptor, while new descriptors are never invented.
   Clipboard/drag/dialog operations remain local until that callback.
5. Format, file, catalog and MLflow controls issue their own API calls. I/O capabilities are
   fetched for each later editor mount, while consumers mounting during one pending fetch share
   that request. Request state is local to the editor; the editor never assumes an out-of-order response still describes a
   changed node unless its own effect/request guards accept it.
6. `EdgeJoinEditor` derives its two role displays from canonical `base`/`join` incoming handles
   and the matching `baseInput`/`joinInput` config. The swap callback is owned by the graph canvas
   because handles and config must move in one graph transaction. Selecting `cross` clears
   `on`/`leftOn`/`rightOn`; selecting another mode exposes either same-name rows (`on`) or paired
   rows (`leftOn`/`rightOn`) and each key-mode switch clears the inactive representation. The
   join type list is exactly `inner`, `left`, `right`, `full`, `semi`, `anti`, `cross`.
   Once both role edges are connected, a non-cross join with no configured
   keys seeds the first known common column exactly once per mounted node;
   existing keys, unknown common columns, and a deliberate user clear never
   trigger another seed.

**Data I/O editors.** `DataInputEditor` is the provider/group orchestrator;
`DataOutputEditor` receives node/graph context and owns explicit writes. The
guarded `/api/io-capabilities` client returns ordered groups, fields,
directional formats/modes/arguments/engines, snapshot build class and schema
requirements, writer class, and publication modes; each mount refreshes it and
only concurrent pending requests coalesce. Test seam:
`resetIoCapabilitiesRequestForTests()` clears the in-flight coalescing promise —
the module's only state. `_IoFormatEditor` renders one
selected group/direction, while dedicated provider sections cover file,
database, lakehouse, Databricks, and inline fields. `OnReplaceConfig` constructs
and commits one fresh active branch for a provider change. Data Input provider
choices are an accent-coloured `radiogroup` of toggle buttons in backend
capability order; an unknown or not-yet-selected provider leaves every toggle
inactive while retaining the explicit configuration error for unknown values.
The editor derives its cache surface from the config through the shared
`dataInputIsDirect` predicate (`frontend/src/utils/dataInputMode.ts`), which
mirrors the backend's `data_input_is_direct`: a file-backed Parquet scan
renders no cache control; every other branch renders the shared
Cache-as-Parquet control. The capability payload still reports each format's
derived `cache_mode` for contract completeness. No cache-mode field is
authored or stored, and a leftover `cacheMode` key is never migrated by the
editor — the backend rejects it as an inactive field. A mode selector is rendered only when the capability advertises more
than one mode; an explicitly stored `scan` does not make a one-option selector
visible.

`DataInputEditor` keeps provider/cache controls in Config while NodePanel hosts
its Polars code in the shared Polars tab. It uses `useSchemaFetch` only when the
selected capability requires a bounded schema, merging detected dtypes into
`arguments.schema` without discarding other arguments. `DataOutputEditor` has
no code panel. Its per-node Zustand entry carries request id, semantic request
identity, phase, and structured result/error; request-id checks reject late
results. Test seam: `resetOutputWriteStoreForTests()` clears every per-node
write entry and the request-id counter. Destination preview comes from `/api/pipeline/output-destination`,
and write identity is projected from the semantic flattened graph, output
node, execution source, and streaming settings. A 409 becomes
`confirm_overwrite`; only that action retries with `overwrite=true`.

Every snapshot-backed provider renders `InputSnapshotCacheButton`, which adapts
the same `CacheFetchButton` presentation and Cache-as-Parquet labels used by
Quote Input to the input-cache API. Missing snapshots offer `Cache as Parquet`
and a not-cached hint, while ready snapshots offer `Refresh Cache` with
generation statistics and a clear action. Direct Parquet renders no cache
control; a stored `read`-mode Parquet input is snapshot-backed and renders
the cache control like any other snapshot input. Snapshot build classification is execution metadata and is not shown
as technical diagnostic copy in the editor. Builds use `lazy_sink`, except admitted-eager formats use
`preview_eager`, and refresh a ready snapshot. The adapter polls jobs to a
terminal result, allows the active button action to cancel the current job,
and keeps stale readiness reactive so `Source changed since cache — Refresh to
update.` remains visible. Required source fields gate all build actions.

**Shared path picker.** `PathPickerField` supplies Preview Data, external
model-file, and registry-defined path fields with one interaction contract:
an empty value immediately shows `FileBrowser`; a selected value renders a
green confirmed-path pill and hides the browser until its `change` action is
pressed (then `close` restores the collapsed state). Browser selection calls
`onSelect` and collapses the picker. Its preserved `file-change-btn` test id
is the API preview contract. While expanded, the picker remains the sole
selected-path summary; its embedded `FileBrowser` does not repeat the path.
Registry-defined input paths are browser-only, matching Preview Data. Output
destinations may additionally enable committed manual entry because their
target file need not exist yet.

**Shared Polars tab.** Known non-instance Data Input, External File, Scenario
Expander, Rating Step, and Model Score nodes expose `Config`, `Polars`, then
(when applicable) `Columns` tabs. Their supplementary code is rendered only in
the shared `PolarsCodePanel`; Config retains only the node-specific settings,
and switching nodes returns to Config. The panel retains the node's code, error
line, input sources and upstream columns, while its hint is node-specific React
content so code-formatted variable names remain semantic.

An ordinary Polars transform whose `code` field is absent derives one advisory,
editor-local starter line from the canonical `InputSource` order:
`# df = <first input name>`. This is real selectable editor text, not a
CodeMirror placeholder, but it is not persisted until the user edits it. Leaving
it untouched therefore preserves the fail-loud empty-transform contract;
deleting `# ` commits a minimal executable pass-through for preview. Once a
`code` value has been committed, including the explicit empty string, that value
wins so clearing the editor does not reinsert the starter. No starter is shown
when there are no inputs, any input is unresolved, or any input is named the
reserved output `df`, because those states cannot produce a valid runnable
suggestion. API inputs follow this same rule rather than receiving a hard-coded
example.

**Column-selection draft semantics.** `selected_columns: []` remains the
committed sentinel for all columns. The Columns tab's None action instead
holds an editor-local zero-selection draft, unticks every row, shows a
`Select at least one column to apply.` status, and does not write config.
Re-ticking a column resumes commits; selecting every available column
normalises back to the empty all-columns sentinel. Value-equal selections do
not commit, and an external config change invalidates an obsolete local draft.
A config update that changes only `selected_columns` invalidates the
post-filter `_columns` result but retains `_availableColumns`: selection cannot
change the pre-filter schema, and retaining it keeps the selector stable while
the refreshed preview is pending. Other config edits continue to clear both
column caches. The pane communicates selection through checkbox state and the
selected-count summary; it does not expose the internal `.select()` operation.

**Banding/Rating classification and canonical formats.** `utils/banding.ts`
classifies only plain objects with recognised continuous, categorical, or
breakpoint modes. Recognised non-blank outputs contribute ordered valid levels
or a named zero-level issue; invalid containers and unknown modes invent
nothing. `RatingStepEditor` uses the complete configured-output set so a broken
configured factor cannot be repopulated from stale raw/saved levels. Rating
reads/writes only `combinedOutputs` and canonical entry rows; Output builds
only complete `outputMapping` rows; API Input builds only `tables`.
`NODE_TYPE_META` supplies those canonical defaults, and Optimiser Apply derives
mode only from persisted `params.mode`.

**Explore toggle-card presentation.** `ExploreToggleCard` owns the common Overview, Pivot, and
Chart enabled/disabled presentation. Its card body is a button with `role="checkbox"` and
`aria-checked`; clicking anywhere in that body toggles only the card's visibility. Enabled cards
use `--accent-soft`, the Explore border, and the Explore label colour, while disabled cards use
`--bg-input`, the neutral border, and primary label colour. Overview cards use the body as the
whole card. Pivot and Chart cards place Delete and Configure in an adjacent action region outside
the checkbox button, so either action leaves `enabled` unchanged.

**Explore chart-card workflow.** `parseExploreCharts` mirrors the backend chart trust boundary:
every card must be complete version 1 (versionless cards are rejected, never migrated), all
known nested fields are
validated, and unknown simple-literal fields are retained. `Add Chart` writes the first unused
`chart_N`, first unused `Chart N` name, `enabled: true`, `pivot_id: null`, `kind: "combo"`, empty
encodings/overrides, Rows category defaults, automatic primary/secondary axes, and bottom legend.
The card label is its persisted name. Its labelled toggle-card body updates only enabled;
Configure and Back change only navigation view state, never card config: the configured chart
id lives per node in `useUIStore`, entering Configure stores it, Back clears that node's
configured id, neither touches the preview pane, and pane
switches on either side preserve a stored id so returning to the editor reopens the same
subview. Delete asks for confirmation, removes only
the selected card, allowing a no-longer-needed PivotChart dependency to be released
deliberately, and clears the card's stored configured id.
The Chart and Pivot list views render their card rows, list headers, Add actions, and empty states
through `ExploreConfigCardList`; that component composes the same `ExploreToggleCard` used by
Overview so enabled-state visuals, click targets, and accessibility cannot drift across panes.

Configure parses the same node's ordered pivots and lists every pivot, irrespective of pivot
visibility. Each option shows the pivot name plus a hidden suffix where applicable and never a
status suffix; source state (unconfigured/loading/error/stale/not calculated/ready) is
communicated by the status messaging in the Configure body, not the picker. The ready state applies the same client identity gate as the
Explore preview: a retained pivot result counts as ready only when the retained Explore result's
`configHash` matches the current graph/source identity, its `dataframe_cache_key` matches the
pivot result's, and the pivot's calculation identity matches; a retained result from a superseded
identity reports stale, never ready. Chart Configure edits chart formatting only — it renders
no pivot field well, field summary, or disclosure box; pivot structure is edited exclusively in
the Pivots editor. The configured chart/pivot subview ids are per-node view state in
`useUIStore`; deleting a card clears its stored id. While chart Configure is open it mounts the
shared
per-pivot auto-update scheduler for its resolved source, claim-serialised with any mounted
result pane. Selecting an initial source atomically seeds one explicit encoding per Pivot
Value in the Combo default arrangement — columns with the last Value as an ungrouped primary
line, a single Value as one plain column — so a newly sourced multi-Value chart opens on the
gallery's leftmost, default Combo option (a single-Value chart's plain-column seed reads as
Clustered columns). Navigation alignment is preview-driven: selecting Pivots or Charts in the
lower preview aligns this editor to the matching pane, while editor-side pane selections,
Configure entry, and Back never modify the preview pane. Pivot Values added after selection
are reconciled on render
(`reconcileValueEncodings`): their controls appear immediately with seeded defaults and a
defaults-applied note, and the seeded encodings persist as part of the next committed chart edit
— one undoable step, no effect-driven write. Changing a populated source uses a confirmation
dialog; cancel changes nothing,
confirm replaces `pivot_id`, encodings, and overrides in one `onUpdate` call. With no pivots, an
explicit action selects the Pivots editor pane without modifying config.

The chart-type gallery renders one labelled icon button per option — Combo leftmost (the
general category and default, as in Excel), then clustered columns,
stacked columns, and 100% stacked columns — with
`aria-pressed` on the type
`detectChartPreset` reports; detection is total, so exactly one button is always pressed and
there is no separate Custom indicator. Any arrangement outside the three column layouts —
lines, mixed marks, secondary-axis series — reads as Combo, and clicking Combo seeds the
classic columns-plus-last-Value-line starting arrangement for the per-Value chart-type and
axis selects to refine. Activating a preset applies it
atomically
(`applyChartPreset`), which rewrites Value styles, clears exact overrides, and never changes
chart orientation. The 100% preset always sets the primary axis number format to `percent`;
every other preset resets a primary format of `percent` back to `inherit` and leaves any other
primary format (for example a currency) untouched — deterministic in both directions, so
restored raw values are never displayed as percentages and non-percent user formats survive
preset changes. A
separate orientation toggle commits `orientation` between vertical columns and horizontal bars.

Per-series controls edit chart type (column/line/area), axis, stacking, colour, markers, and
labels per Value. Exact-series overrides nest beneath their owning Value box as a collapsed
"Series overrides (N)" disclosure rendered only when a result exists and either the source
pivot has Columns (one Value fans out into several series) or that Value already has
overrides; a single-series Value renders no override surface, because its Value box is the
series configuration. Expanding the disclosure lists that Value's concrete series to create or
edit an exact override or reset it
to the Value default.
New exact overrides allocate the first unused `override_N` id across both Value encodings and
existing overrides, preserving the card-wide nested-id uniqueness invariant.

The stacking control is a per-series select — None, Stacked, 100% stacked — whose every
transition commits once and lands on a card the validators accept: None clears that series'
`stack_group` and `stack_normalize` only; Stacked or 100% on an ungrouped series joins the
chart's sole existing group on the same axis (rewriting the whole group's `stack_normalize`
when the chosen mode differs) or otherwise allocates the first unused `stack_N`; switching
Stacked ⇄ 100% on a grouped series rewrites `stack_normalize` on every member of the group;
and committing a different axis on a grouped series clears its group membership in the same
commit — a group never spans axes. A group-name input appears only when the chart already has
more than one stack group: renaming atomically rewrites every member of the current group, a
rename onto another group commits only when that group's axis and normalisation agree
(a compatible merge), and an incompatible rename shows an inline validation error and persists
nothing.

The colour control is a swatch row — an Automatic reset, the theme series palette, and a native
colour input for custom values — persisting `#RRGGBB` or null with no free-text entry. Swatch
and Automatic clicks commit immediately; the custom input is a committed control that tracks the
picker's streamed change events as a local draft and persists once on blur, so a drag through
the native picker never commits per tick.

The Configure body is ordered: chart-type gallery, orientation toggle, axis formatting, the
Legend box, then
the per-Value boxes. Axis formatting is two bordered boxes — Primary axis and Secondary axis.
Each contains that axis's title, minimum, maximum, and number-format controls; axis inputs
commit valid finite
bounds with minimum less than maximum, closed number formats (the `inherit` option is labelled
`General (automatic)`), and titles. The Secondary box is headed by a "Use secondary axis"
checkbox bound to `axes.secondary.enabled`: its fields render only while ticked; unticking
commits one edit that disables the axis and moves every secondary-assigned style back to
primary (clearing stack membership per the axis-change rule); while unticked the per-series
Axis selects offer no Secondary option, so a disabled-but-used state is unreachable and
assigning a series to the secondary axis first requires re-ticking it. The
Legend box follows the Secondary axis box and is headed by a "Show legend" checkbox bound to
`legend.visible`; the position select renders inside it only while ticked. Category rotation
and the grand-total opt-in remain committed controls after the per-Value boxes. Dormant encodings and overrides
are explicit and described by name — a dormant override by its decoded series label
(`exploreChartSeriesLabel`), a dormant encoding by its Value display name or as a removed
Value — never by an internal id. A pivot Value without a persisted encoding renders seeded
default controls with a
defaults-applied note rather than a dead-end diagnostic, and no editor action silently selects a
source or invents a replacement mapping.

**Explore pivot-card workflow.** `parseExplorePivots` is the frontend trust boundary matching
`validate_explore_pivots`: every card must be complete version 1 (versionless entries are
rejected, never migrated); it validates
known nested fields, preserves unknown simple-literal fields, and rejects duplicate ids,
case-insensitive names, or placement ids. `Add Pivot` writes the first unused `pivot_N`, first
unused `Pivot N` name, `enabled: true`, four empty zones, and both grand totals enabled. A card's
label is its configured name. Its labelled toggle-card body updates only `enabled`; `Configure`
and `Back to pivots` change only navigation view state, never card config, with the same
per-node `useUIStore` lifecycle as chart cards: entering Configure stores the configured pivot
id, Back clears the stored id, neither touches the preview
pane, pane switches preserve it, and deleting the card clears it.

The Configure subview receives `upstreamColumns: {name, dtype}[]` from `NodePanel`. It has a
committed name input (blur/Enter), a case-insensitive search input, a fixed-height scrolling field
box, and four ordered zones. Every compact field row renders its name and dtype followed by
`Add to:` and native Filters/Columns/Rows/Values buttons. A row action commits that one field
directly to its target in one graph edit; there is no checkbox selection state or shared action
block beneath the field box. Filters/Columns/Rows disable the matching row action when that field
already exists in the target; Values always adds another stable placement. The four assigned-field
areas render beneath `Drag fields between areas below:` as a two-column CSS grid in canonical
Filters, Columns, Rows, Values order. Each placement card is native-HTML draggable and writes the
namespaced `application/haute-pivot-placement` transfer payload. While a valid target handles
`dragover`, it sets the move drop effect and exposes its active drop state. Dropping on a placement
inserts before that placement; dropping on the remaining area appends. The positional move removes
the current source placement, converts it with the same target-zone rules when crossing areas,
normalises the insertion index after source removal, and commits exactly once. Same-area drops
reorder when they change the position. Duplicate Filters/Columns/Rows targets do not accept a
cross-area drop; Values accepts repeated fields. Visible Move-to and Move-up/down controls are not
rendered. Placement cards are focusable: Up/Down reorder and Left/Right move to the previous/next
canonical area when valid, while nested aggregation/filter controls retain their native keys.
Remove remains a separately named button. A field may occur in different zones. Every Value
addition gets a new
first-unused placement id and repeated Values are allowed. Numeric dtypes default to `sum`;
all other dtypes default to `count`. Value aggregation changes are committed selects and expose
only compatible operations. Numeric detection reuses the shared Polars dtype helper and recognises
full names, short aliases, and Decimal. Numeric Values expose all seven operations; scalar
non-numeric Values (including Binary and Duration) expose count, distinct count, min, and max;
nested List/Array/Struct and Object Values expose count only. The filter-member picker loads its
initial list immediately, debounces non-empty searches by 250 ms, and aborts obsolete requests.
A displayed member list is keyed to the node's current Explore cache identity hash (the same
graph/source gate the Explore preview applies) as well as the field/search pair, so when the
graph or source changes the previous dataset's members stop being rendered (and selectable)
immediately rather than lingering until the replacement response lands. Display-only pivot and
chart edits do not change that identity, so selecting a member neither hides the remaining
choices nor triggers a redundant member reload.
Row placements persist `sort: "ascending" | "descending"`, defaulting
to ascending while parsing older v1 cards. Value placements persist
`sort_rows: "none" | "ascending" | "descending"` and
`color_scale: "none" | "low_red_high_green" | "low_green_high_red"`, both defaulting to none.
`options.sort_by` persists the selected Row/Value placement id or null for default ascending Row
labels. Older v1 cards derive it from their sole active Value sort, otherwise null. Placement cards
render none of these controls. A full-width Sorting section after the area grid selects the target
and direction; changing it atomically resets dormant Row directions to ascending and every
non-target Value's `sort_rows` to none. A full-width Conditional formatting section follows it as
a bordered rules box. Its ordered list is derived from Value placements whose `color_scale` is not
none, so all persisted rules are simultaneously visible in Value-placement order. Each rule row
contains a Value selector, a scale selector, its Low/High preview, and an explicitly named Remove
button. Selector options contain the row's current Value plus compatible numeric Values without an
existing rule; changing it atomically moves the scale to the new Value and resets the former Value
to none. `Add rule` selects the first compatible unformatted Value in placement order and assigns
`low_red_high_green`; it is disabled when none remains. Removing sets that Value's scale to none.
Changing an aggregation to a non-numeric result atomically resets its colour scale to none. Empty,
ineligible, and fully-configured states explain why no additional rule can be added. Removing or moving the active sort
target outside Rows/Values clears `options.sort_by`; crossing between Rows and Values preserves the
target with a valid direction for its new zone. Effective sort changes affect
`pivotCalculationIdentity`; `sort_by` choices with identical effective ordering and colour-scale
changes do not.
Missing upstream fields remain in place with `aria-invalid` and an
explicit unavailable-field message. The Configure subview has no preview/refresh callback:
committed calculation changes are observed by a mounted lower Pivots or Charts pane, which
automatically schedules the affected Pivot calculation. Presentation-only names and Chart
appearance remain outside the Pivot calculation identity and rerender from retained results.

**Explore pane ordering.** `NodePanel` declares exactly five Explore panes in this order:
`code`, `overview`, `pivots`, `charts`, `export`; `relationships` is not a valid `ExplorePane`.
The Pivots tab sits between Overview and Charts and renders `ExplorePivotsConfig` in its labelled
tabpanel. The active `ExplorePane`, including `pivots`, is stored by node id in
`useUIStore.explorePanes` so switching to another node and back restores the same selection.

## Edge cases and invariants

- Column selectors accept unavailable/empty preview schema through their editor-specific text or
  persisted-value route; a known value is not silently erased just because upstream preview data
  changed.
- Explore chart ids are stable persistence identities, while the visible `Chart N` label follows
  array order. Adding or toggling a chart is display-only and must not invalidate the Explore
  dataframe/report cache or increment the graph's execution-structural version.
- Explore pivot ids are stable persistence identities and names are persisted display identities.
  Adding, naming, toggling, or configuring a pivot is display-only and must not invalidate the
  Explore dataframe/report cache or increment the graph's execution-structural version.
- Schema/output rows that are persisted but incomplete stay editable. Fresh inferred rows can be
  filtered/merged before persistence without making existing user rows disappear.
- Rating table normalisation supports missing/malformed entries by producing the editable table
  contract; two-way grids keep their cartesian factor coordinates aligned with their entry values.
  It preserves canonical row order and valid `factorDtypes` metadata instead of dropping either
  during a view-only open/save cycle.
- `frontend/src/panels/editors/shared/tableClipboard.ts` parses tab/newline data before applying
  it, while the rating/banding grids validate their target coordinates and numeric values.
- Path tools preserve/rewrite only recognised path prefixes. JSON path validation and
  canonicalisation hints are advisory client checks, not an alternative backend execution grammar.
- Input chips and live-switch mapping rows are keyed by `edgeId`, so a removal can never route
  to the wrong edge. Live-switch rows display `name` (with the same `frameUnresolved` warning
  state as chips) and read/write `input_scenario_map` by that same `name` — two frames from one
  API input are two distinct map keys, individually routable for the first time. A frame
  rename's atomic commit updates every name-referencing config in the same pass that rebinds
  the edges: `input_scenario_map` keys on downstream live-switches, instance `inputMapping`
  **values** on downstream instance nodes, and instance `inputMapping` **keys** on every node
  whose `instanceOf` references an affected *original* (the original's input names are the
  mapping keys, so renaming a frame feeding the original would otherwise stale-key every
  instance). The commit is **preflighted**: before anything mutates, every affected target's
  post-rename input-name set is checked for duplicates (the new name colliding with another
  input already on that target), and a collision rejects the entire rename with an inline
  error naming the target and the colliding name — no config, edge, or mapping mutation
  occurs. Name-referencing config can never half-apply or overwrite an existing key.
- An ordinary source-label rename derives the old and new `edgeInputName` for every outgoing
  edge and uses the same duplicate preflight and atomic mapping migration as an API-frame rename.
  Sanitisation-only no-ops do not rewrite mappings.
- `edgeInputName` resolves an editor-only edge sourced by a drilled submodel's composite Input by
  matching the edge's opaque `sourceHandle` to the existing `SubmodelBoundaryPort.id`, then
  sanitising that port's `label`. A missing handle, non-Input boundary, or unknown row is an
  invariant violation and throws rather than falling back to the composite node's literal
  `INPUT` label.
- `edgeInputName` treats only API-input sources' handles as frame names; a submodel
  `out__`-prefixed source handle resolves to the referenced child node's sanitised label (via
  the graph context's `submodels`) — the same name the flattened code binds — and every other
  node type derives the sanitised source label.
- The API-input editor rejects a frame label that fails backend invariant B4 (not an ASCII
  identifier, or a Python hard keyword) at commit time with the same inline validation used
  for blank/duplicate labels (`apiInputLabelIssue` — the exact ASCII mirror) — the label is
  the downstream argument name, so an invalid label never reaches the config.
- WebSocket graph updates retain rejected edges for repair, so a null-handle API-input edge can
  reach the panel even though the canvas cannot author one. Its chip and output block use the
  `<unresolved>` marker with `frameUnresolved`; a named stale handle is retained verbatim. Neither
  state aliases the source's sole emitted table.
- `frontend/src/panels/editors/OutputEditor.tsx`'s per-frame block label is the edge's input
  name via the same shared helper, and the persisted `source_port` key (`framePortId`) now
  *equals* that name by construction — the frame label for API-input edges, the sanitised
  source label otherwise — so display and persisted identity cannot diverge. An unresolvable
  API-input edge renders the block header in the explicit unresolved state (parent label
  retained as identifying text plus a visible warning marker), never a normal-looking fallback.
- Edge Join roles are never editable ids: they come from role-bound edges and can only be
  exchanged by the atomic swap action. Cross joins persist no keys. Non-cross joins use either
  a non-empty normalised `on` list or equal-length non-empty `leftOn`/`rightOn` lists; both forms
  cannot coexist. The UI lists all and only the backend-supported modes: `inner`, `left`,
  `right`, `full`, `semi`, `anti`, and `cross`.

(The former NOTE here — two frames of one API input sharing one sanitised `varName`, leaving
`input_scenario_map` unable to distinguish them — is resolved by the input-identity
convergence: scenario-map keys are now the frame-derived input names, and the backend's
matching in `executor.py`, `projection.py`, and the deploy pruner consumes the same
`edge_input_name` derivation.)

## Error handling

Editor-local API failures are rendered as their respective lookup/action error state. Invalid
input is marked by the control or rejected at its parse/normalisation point. Unknown node types,
broken instance configuration and unrecognised IO options are surfaced visibly by panel/editor
diagnostics; no generic editor fabricates a replacement config.

## Testing

React/Vitest tests cover editor interaction under `frontend/src/__tests__/editors/`,
`frontend/src/panels/editors/__tests__/`, `frontend/src/panels/editors/banding/__tests__/`,
`frontend/src/panels/editors/rating/__tests__/`, `frontend/src/__tests__/components/form/`, and
`frontend/src/components/form/__tests__/`. They exercise committed text/code/grid edits, IO/API
schema paths, output paths, banding and rating editing, clipboard-related grid behaviour,
Databricks/MLflow selection, panel dispatch/lazy loading and accessibility. There is no dedicated
test file for every small barrel/style/helper module; those are covered through their consumers.
`frontend/src/__tests__/editors/ExploreChartsConfig.test.tsx` pins add/configure/back/toggle,
independent multi-card state, stable id allocation, future-field preservation, and malformed-state
diagnostics; `frontend/src/panels/__tests__/NodePanel.test.tsx` pins the lazy Charts-pane dispatch.
`frontend/src/__tests__/editors/ExplorePivotsConfig.test.tsx` pins add/configure/back, multiple
cards, stable id allocation, future-field preservation, malformed-state diagnostics, and the
absence of a speculative toggle. The same NodePanel suite pins the five-pane ordering, absence of
Relationships, Pivots-pane dispatch, and per-node Pivots selection memory.
`frontend/src/__tests__/editors/EdgeJoinEditor.test.tsx` pins fixed role displays and swap
availability, all seven join options, same-name/asymmetric mode transitions, automatic key
clearing for `cross`, advanced Polars options, and visible diagnostics for conflicting role/key
state.

The input-identity work is pinned by `frontend/src/panels/__tests__/NodePanel.test.tsx`
(`name` derivation for API-frame edges — sole frame included — ordinary sources, and submodel
`out__` edges resolving to child labels; the `frameUnresolved` warning chip for a
zero-eligible-frame API source; the unresolved→resolved transition under an unchanged name
string clearing the warning; signature-driven refresh on a frame rename; two frames from one
API input rendering two distinct, independently removable chips whose names equal the
generated argument names), by LiveSwitch cases (two frames from one API input render two rows
with two distinct names and two independent `input_scenario_map` keys; a frame rename migrates
its map key atomically with the edge rebind; a zero-eligible-frame API `InputSource` renders
the row's unresolved warning marker/tooltip), by the ApiInputEditor identifier-validation
cases (non-identifier and keyword labels rejected inline before commit, and a rename
collision preflight rejection surfaced inline via the `OnUpdateConfig` result with the graph
asserted unchanged), by the editor suites
that render `InputSourcesBar` (`ModelScoreEditor`, `OptimiserApplyEditor`,
`ScenarioExpanderEditor`, `BandingEditor`, and the hover suite), by the OutputEditor suite's
name-equals-`framePortId` and unresolved-block-header cases, and by
`frontend/src/utils/__tests__/apiInputPorts.test.ts` for the shared `edgeInputName`
derivation. Because `InputSource.name` replaces the former `varName`/`displayLabel` pair,
every suite constructing `InputSource` fixtures (Transform, RatingStep, LiveSwitch,
ScenarioExpander, ModelScore, OptimiserApply, Banding, ExternalFile, ExploreCode, and the
hover suite) migrates its fixtures — a compile-time-loud migration, not a runtime fallback.

Browser coverage for authoring flows is in `frontend/e2e/data-io-nodes.spec.ts`,
`frontend/e2e/persistence/api-input-render-gate.spec.ts`,
`frontend/e2e/persistence/api-input-v2-native.spec.ts`, and
`frontend/e2e/persistence/api-input-frame-alignment.spec.ts` (downstream frame-naming chips
alongside its canvas geometry assertions).

`frontend/src/__tests__/editors/ApiInputEditor.test.tsx` pins the structured-file extension
filter and XML cache action. `frontend/src/panels/editors/__tests__/_shared.test.tsx` pins
navigation/rendering for a directory whose `size` is null and ensures no numeric/`NaN` size is
shown.

The Data I/O component matrix covers guarded capabilities, ordered provider
groups and fields, dependency/build/snapshot states, atomic provider changes
and undo, schema merge/preservation, output direction and code-panel
exclusion, destination mismatch, pending-write remounts, structured outcomes,
and overwrite confirmation. `frontend/e2e/data-io-nodes.spec.ts` exercises the
canonical nodes through save/reload, snapshot/offline execution, and write.
`frontend/e2e/persistence/api-input-v2-native.spec.ts` remains a canonical API-frame schema-continuity
suite; it does not test or preserve v1 migration behavior.

The initial Banding-to-Rating configuration-shape matrix is:

| Variant | Owning component | Representative fixture/contract | Smallest proving tier | Browser escalation |
|---|---|---|---|---|
| Continuous Banding | `frontend-node-editors` | `frontend/src/panels/editors/banding/__tests__/BandingRulesGrid.test.tsx::makeFactor` plus continuous render/edit/copy cases | Component | None; behaviour is local to one grid. |
| Categorical Banding | `frontend-node-editors` | The same factory with categorical rules and value/match-count cases | Component | Included only as one factor in the mixed journey. |
| Breakpoint Banding | `frontend-node-editors` | `frontend/src/panels/editors/banding/__tests__/BreakpointGrid.test.tsx` boundary/label/order fixtures and `frontend/src/__tests__/editors/BandingEditor.test.tsx` mode cases | Component | Included only as one factor in the mixed journey. |
| Mixed three-factor Banding→Rating | `frontend-node-editors` | Generated `browser_mixed_banding.json` and `browser_rating.json` from `run_frontend_e2e_server.py` | Browser | Authoritative cross-editor Cartesian rebuild, edit, save, and reload journey. |
| Zero-level configured factor | `frontend-modelling-optimiser-ui` | `frontend/src/__tests__/utils/banding.test.ts` zero-level classifier plus `frontend/src/__tests__/editors/RatingStepEditor.test.tsx` warning/no-stale-level case | Unit + component | None; a deterministic warning contract needs no browser duplication. |
| Malformed or partial draft | `frontend-modelling-optimiser-ui` | `frontend/src/__tests__/utils/banding.test.ts` malformed-default, blank-output, and partial-rule cases | Unit | None; invalid drafts are classification inputs, not a persistence journey. |
| Mixed Rating outputs | `frontend-node-editors` | `frontend/src/__tests__/editors/RatingStepEditor.test.tsx` multi-table `combinedOutputs` selection/duplicate/output cases | Component | None until a cross-node persisted failure is found. |
| Persisted Rating table | `frontend-node-editors` | Generated three-factor fixture and eight edited Cartesian entries | Browser | Save/reload assertion is the owning evidence. |

`frontend/e2e/canvas-assurance.spec.ts` rebuilds eight Cartesian entries from
three two-level factors, edits a relativity, saves and reloads it, and captures
element-scoped 1440×900 and 1024×768 snapshots. Its optimiser selection
journey captures the same two supported viewport sizes. The protected Rating
rebuild is explicitly focused and activated with Enter, editing leaves with
Tab, and the save uses the platform keyboard shortcut.

The accessibility-automation boundary is a recorded no-new-dependency
decision: native semantic queries and explicit ARIA/focus assertions remain
blocking in Vitest, and the one stable cross-editor keyboard journey remains
blocking in Playwright. Screenshots protect visual state but are not treated as
an accessibility audit. A blanket axe-style scan is not added because the
repository has not defined a whole-application conformance claim or an owned
exception policy; reconsidering one requires a small named route set, rule
scope, browser, exception owner, and expiry rather than silently accepting
scanner noise. Canonical Rating/Output/API Input interaction tests continue to
cover new/edit/save/reload shapes; there are no migration-specific fixtures.

## Modelling config panes

The behaviour and non-goals are defined by
[the modelling/optimiser UI contract](../frontend-modelling-optimiser-ui/high-level.md#modelling-config-panes).

`frontend/src/panels/NodePanel.tsx` renders a five-pane modelling strip (Target, Features, Params,
Split, Train) with the shared preview tab control and per-node UI-store selection memory. It is
shown only when the modelling node has a supported
`catboost` or `glm` algorithm; an unset algorithm leaves the gateway as the only editor content.
A non-empty unsupported value also suppresses the strip and is handed to the modelling editor's
explicit diagnostic rather than treated as CatBoost.

`NodePanel` reads the remembered pane and selects only the Boolean presence of
`trainJobs[node.id]`. Its tab descriptors leave Target/Features/Params/Split as plain labels and
add only the active indicator on Train. Configuration completeness is deliberately not derived or
displayed by the node panel; `ModellingConfig` owns click-time validation beneath its Train
button. There is no child-to-parent registration or effect, so node changes cannot flash a
previous node's state and progress-only updates do not rerender the panel chrome. The active tab
panel keeps the shared `id`/`aria-labelledby` relationship.

`frontend/src/panels/__tests__/NodePanel.test.tsx` proves unset/supported/unsupported-algorithm
strip gating, all five routes, same-node memory, independent memory for two nodes, plain setup-tab
labels, active-indicator routing, and no stale active state after a node change. The generic
indicator and keyboard
contract is owned by
[frontend-preview-explore](../frontend-preview-explore/low-level.md#modelling-config-panes).
