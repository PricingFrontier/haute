# Frontend Node Editors — Low-Level Specification

## Module map

### Panel shell / dispatch

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/PanelShell.tsx` | Shared right-side panel wrapper for every panel in the app (NodePanel, UtilityPanel, ImportsPanel, GitPanel, TracePanel): width (from `useUIStore`, defaulting to 50% of available space), drag-to-resize with min/max clamping, slide-in animation, optional inlined `PanelHeader`. |
| `frontend/src/panels/PanelHeader.tsx` | Generic header bar (icon, title, subtitle, actions, close button) used by `PanelShell` for panels other than `NodePanel` (which builds its own bespoke header inline). |
| `frontend/src/panels/NodePanel.tsx` | The node-configuration panel: node-type dispatch, the Config/Columns tab bar, Explore-node pane bar, stale-columns/schema-warning banners, the Instance and Unknown-type diagnostics, and the label/refresh/close header row. |
| `frontend/src/panels/NodePalette.tsx` | Left-side draggable list of node type templates; sets the HTML5 drag payload consumed by the canvas to create a new node. |
| `frontend/src/panels/LazyNodeEditors.tsx` | Single indirection point: every editor body is `React.lazy`-imported here and re-exported; also defines `LazyEditorBoundary` (a `Suspense` wrapper with a pulsing placeholder). |
| `frontend/src/panels/useGraph.ts` / `frontend/src/panels/GraphContext.tsx` | Context + hook exposing `{allNodes, edges, submodels, preamble}` to `NodePanel` and any editor that needs whole-graph context (`SinkEditor`, `EdgeJoinEditor`, `RatingStepEditor`'s factor-level extraction, the Instance panel). Throws if used outside a provider. |
| `frontend/src/components/ReadOnlyNodeConfig.tsx` | Read-only rendering of the same editor set (no-op `onUpdate`, empty `GraphProvider`) for comparison views; a deliberately separate switch statement from `NodePanel.renderEditor`. |
| `frontend/src/components/KeyPickerModal.tsx` | The one shared dialog behind `ApiInputEditor`'s inherit, inherit-attributes, and cascade key-pick step: a collapsible grouped candidate-key checklist (paths already on the target rendered checked+disabled) plus an optional "enter a field by hand" section, portalled to `document.body` (not `PanelShell`) so its backdrop isn't clipped by the side panel's transformed ancestor. Presentational only — the caller supplies the groups and owns what "confirm" does. |

### Editor barrel and shared primitives (`frontend/src/panels/editors/`)

| File | Responsibility |
| --- | --- |
| `index.ts` | Public barrel: re-exports each editor's default plus shared types (`SimpleNode`, `SimpleEdge`, `InputSource`, `SchemaInfo`, `OnUpdateConfig`) and shared components (`FileBrowser`, `SchemaPreview`, `InputSourcesBar`, `CodeEditor`, Databricks pickers). |
| `_shared.tsx` | Shared types, `INPUT_STYLE`/`SELECT_STYLE` constants, `MlflowStatusBadge`, `FileBrowser` (directory-walking file picker with a file-list cache), `SchemaPreview` (column table + expandable row preview). |
| `_DatabricksSelector.tsx` | `WarehousePicker`, `CatalogTablePicker` (cascading catalog→schema→table selects), `DatabricksFetchButton` (wraps the shared cache-fetch-button pattern for Databricks cache lifecycle). |
| `_IoFormatEditor.tsx` / `_ioFormats.ts` | Registry-driven editor body shared by `DataInputEditor`/`DataOutputEditor`. Format list, per-format capability (modes, argument names, missing-engine flags, source kind) all come from `GET /api/formats`, module-cached. |
| `CodeEditor.tsx` / `CodeMirrorEditor.tsx` | `CodeEditor` is a thin `React.lazy` wrapper; `CodeMirrorEditor` is the actual CodeMirror 6 instance (Python mode, Haute theme, column-aware string autocomplete, error-line diagnostics, debounced external/local value sync). |
| `shared/PolarsCodePanel.tsx` | Composition of `InputSourcesBar` + label + `CodeEditor` + static `return df` hint, used by `TransformEditor` and `ExploreCodeEditor`. |
| `shared/tableClipboard.ts` | `parsePastedGrid`, `buildTsv`/`buildCsv`, `writeClipboardText`, `downloadTextFile`, `clipboardWriteAvailable` — copy/paste/export primitives shared by `FrameTableActions` and `JsonPreview`. |
| `FrameTableActions.tsx` | Shared Copy/Share/Save(JSON,CSV,TSV)/Paste toolbar for grid-shaped config (output-mapping tables, API-input tables). Format-agnostic: host supplies `getGrid`/`getSchema`/`onPaste`. |
| `JsonPreview.tsx` | Reusable expandable JSON viewer (loading/error/empty states, truncation note, copy/export) used for the output-mapping assembled preview and per-frame input-data previews. |

### Per-node-type editors (`frontend/src/panels/editors/`, dispatched from `NodePanel.renderEditor`)

| Node type | Editor file(s) | Summary |
| --- | --- | --- |
| `apiInput` | `ApiInputEditor.tsx`, `apiInputInherit.ts`, `apiInputSchema.ts` | v2 schema-mapping editor: shreds an incoming API/quote JSON document into one or more named "frame" tables of typed columns via JSONPath locations; supports cascading/inheriting keys across frame levels and confirm-gated re-inference. |
| `liveSwitch` | `LiveSwitchEditor.tsx` | Maps each connected input to a named data source, used to route between live and batch data. |
| `dataSource` | `DataSourceEditor.tsx` | Flat-file or Databricks source, plus optional post-load Polars code. |
| `dataSink` | `SinkEditor.tsx` | Output format + path, with an in-panel "Write" action that executes the sink immediately via the API. |
| `dataInput` / `dataOutput` | `DataInputEditor.tsx` / `DataOutputEditor.tsx` (thin wrappers over `_IoFormatEditor.tsx`) | Registry-driven generic polars read/write editor. |
| `explore` | `ExploreCodeEditor.tsx`, `ExploreOverviewConfig.tsx` | Code pane (dataset-prep Polars code) and Overview pane (toggle which summary cards render); Relationships/Charts/Export panes are placeholders with no editor wired yet. |
| `externalFile` | `ExternalFileEditor.tsx` | Loads a pickle/JSON/joblib/catboost file and runs user code against the loaded object. |
| `output` | `OutputEditor.tsx`, `outputMappingSchema.ts`, `outputPathTools.ts`, `pathCanonicalWarning.ts`, `jsonpath.ts`, `JsonPreview.tsx` | Maps each incoming frame's columns to paths in the assembled response document; one block per incoming edge/frame; v1→v2 migration; conflict detection; assembled-output dry-run preview. |
| `banding` | `BandingEditor.tsx` + `banding/*` | Bins a numeric or categorical upstream column into named bands (breakpoints or explicit categorical rules), with histogram/match-count feedback from `previewRows`. `BreakpointGrid`/`BandingRulesGrid` accept a pasted range starting from any editable cell (not only the first row), ignore a pasted header row that matches the grid's own copy-header text, and each factor has a one-click "copy as TSV" action (`copyBanding`) mirroring the paste format. |
| `scenarioExpander` | `ScenarioExpanderEditor.tsx` | Cross-joins each row with a range or fixed step count of scenario values. |
| `ratingStep` | `RatingStepEditor.tsx` + `rating/*` | One-, two-, or three-factor lookup tables mapping factor levels to a rate/relativity, plus combined outputs and an optional code override. Three non-persisted UI sections — **Tables**, **Combined**, **Code** (local `activeSection` state only, never written to node config): Tables has a search box plus an "Issues"/"All" filter over per-table healthy/problem status, each table row summarising factor and entry counts; the table editor itself renders as a two-column list (one factor), a row/column grid (two factors), or a third-factor slice selector over a two-factor grid (three factors). Combined mirrors the same selector pattern for `combinedOutputs`. Code is a `CodeEditor` whose autocomplete column list is upstream columns plus every table's `outputColumn` plus every combined output's `outputColumn`, since generated code runs after both stages. |
| `modelScore` | `ModelScoreEditor.tsx`, `MlflowModelPicker.tsx` | Scores an upstream frame with a registered MLflow model or a specific run's artifact. |
| `optimiserApply` | `OptimiserApplyEditor.tsx`, `MlflowModelPicker.tsx` | Applies a previously solved optimiser artifact (`online` lambdas or `ratebook` factor tables) from a file, run, or registered model. |
| `constant` | `ConstantEditor.tsx` | Named constant name/value rows (1-row DataFrame). |
| `polars` | `TransformEditor.tsx` (uses `shared/PolarsCodePanel.tsx`) | Free-form Polars transform code. |
| `edgeJoin` | `EdgeJoinEditor.tsx` | Join type, same-name or paired join keys, suffix, coalesce/validate/maintain-order options; reads whole-graph context via `useGraph()` to resolve base/join role columns. |
| `submodel` | `SubmodelEditor.tsx` | Read-only summary of a submodel reference (file, child node count, input/output ports). |
| Columns tab (most node types) | `ColumnsTab.tsx` | Flat searchable column select/rename list, with stale-column ghost rows. |
| Columns tab (`apiInput` only, currently unreachable via `NodePanel`) | `GroupedColumnsTab.tsx` | Groups columns by dotted-path prefix with array-pattern collapsing (`licence.*.type ×2`) and a segment-strip rename affordance. |

### Rating sub-modules (`frontend/src/panels/editors/rating/`)

| File | Responsibility |
| --- | --- |
| `cellStyles.ts` | Two shared inline-style constants (`EDITABLE_RELATIVITY_INPUT_STYLE`, `NON_EDITABLE_LABEL_CELL_STYLE`) used by both `OneWayEditor.tsx` and `TwoWayGrid.tsx` so the editable rate cell and the read-only row/column label cell look identical across the one-way and two-way grids. |

### Shared node/graph-adjacent utilities (`frontend/src/utils/`)

| File | Responsibility |
| --- | --- |
| `edgeJoinRoles.ts` | Canonical `base`/`join` handle IDs and `baseInput`/`joinInput` config keys for the `edgeJoin` node type, kept in sync with the backend's `_edge_join` module (see file header comment); `edgeJoinCanonicalTargetHandle` also folds the legacy `join-bottom` handle alias into `join`. Shared between this component (`EdgeJoinEditor.tsx`, `edgeJoinValidation.ts`) and the canvas's connection/handle-rendering code. |
| `edgeJoinValidation.ts` | `analyzeEdgeJoinNode` derives the full diagnostic/derived-state model an `edgeJoin` node's editor and save-gate need (role edges, resolved base/join input, key lists, common columns, ordered diagnostic strings) from its config and the live node/edge graph; `findFirstInvalidEdgeJoin`/`formatEdgeJoinValidationIssue` scan the whole graph for the first invalid edge join and format it into a single toast-ready message. |
| `edgeJoinGraph.ts` | Graph-mutation helpers for the `edgeJoin` node type: `insertEdgeJoinNode` splits an existing edge to interpose a new edge-join node (rewriting any downstream edge-join's role config or fan-in `contract.inputs_by_parent` that pointed at the split edge's source), `insertEdgeJoinNodeFromSources` builds one directly from two source-node/handle pairs, `swapEdgeJoinInputs` swaps an edge-join node's base/join roles across both its config and its incoming edges' target handles. All three return a discriminated `{ ok: true, ... } | { ok: false, reason }` result rather than throwing. |
| `banding.ts` | Extracts factor-column → level-name maps from `banding`-node configs: `extractBandingLevelsForNode`/`extractBandingLevels` (one node / all nodes, rule output values only) and `extractBandingLevelOrderForNode` (same, plus the rule's `default` appended last, for stable dropdown ordering). `bandingLevelOrderForOptimiser` resolves an optimiser node's banding source (an explicit `banding_source` config value, else the single directly-connected banding input) and returns its level order. Consumed by `RatingStepEditor.tsx` (factor-level dropdowns) in this component and by the Optimiser component's ratebook factor tables. |

> NOTE: `edgeJoinGraph.ts` and (to a lesser extent) `edgeJoinRoles.ts` are consumed almost entirely
> from the canvas side — `insertEdgeJoinNode`/`insertEdgeJoinNodeFromSources` are called only from
> `App.tsx` and `hooks/useEdgeHandlers.ts` (turning a drag-drop connection or edge-drop into an
> edge-join node), and `swapEdgeJoinInputs` only from `App.tsx`'s context-menu handler — none of
> this component's editor bodies call them directly. `frontend-graph-canvas`'s own spec already
> references `edgeJoinRoles.ts` (handle-id conventions) and `swapEdgeJoinInputs`'s result shape.
> `edgeJoinValidation.ts` is the one file of the three genuinely driven from inside this component
> (`EdgeJoinEditor.tsx` calls `analyzeEdgeJoinNode` directly), plus the save-time gate in
> `hooks/usePipelineAPI.ts`. This looks like a coverage-assignment judgement call rather than a
> clear-cut ownership boundary — see [frontend-graph-canvas](../frontend-graph-canvas/high-level.md)
> for the node-insertion/connection side of the same files.

### Form primitives (`frontend/src/components/form/`)

| File | Responsibility |
| --- | --- |
| `CommittedTextField.tsx` | `CommittedTextField` / `CommittedTextArea` — draft-buffered inputs that commit once on blur (text field also on Enter), the general-purpose base for the commit-on-blur pattern used across the editors. |
| `ConfigCheckbox.tsx` | Labelled checkbox with accent-coloured check, `useId`-generated id when none supplied. |
| `EditorLabel.tsx` | Uppercase micro-label (`label`/`span`/`div`), replaces a previously-repeated Tailwind class string. |

## Key types and data structures

- **`SimpleNode` / `SimpleEdge`** (`_shared.tsx`) — the minimal node/edge shape editors need:
  `SimpleNode = { id, type?, data: { label, description, nodeType, config?, [k: string]: unknown } }`;
  `SimpleEdge = { id, source, target, sourceHandle?, targetHandle? }`. Re-exported from
  `panels/editors/index.ts` and used as `NodePanel`'s own public `SimpleNode`/`SimpleEdge` type
  (preserving App.tsx's contract).
- **`InputSource`** (`_shared.tsx`) — `{ sourceNodeId, varName, sourceLabel, edgeId }`, one per
  upstream edge into the selected node; `varName` is `sanitizeName(sourceLabel)`. Computed once
  in `NodePanel` (`inputSources` memo) and passed to every editor that accepts connections.
- **`OnUpdateConfig`** — `(keyOrUpdates: string | Record<string, unknown>, value?: unknown) =>
  void`, the single mutation entry point every editor is given (`handleConfigUpdate` in
  `NodePanel`). Accepts either a single key/value pair or a batch of updates, so a multi-field
  gesture (e.g. edge-join's join-type change resetting `on`/`leftOn`/`rightOn`) is one config
  write, one undo step.
- **`HauteNodeData`** (`types/node.ts`) — the typed shape of a React Flow node's `data`:
  `label`, `nodeType`, `config?`, plus cached preview-result fields (`_columns`,
  `_availableColumns`, `_schemaWarnings`, …). `effectiveNodeType(node)` resolves the type from
  `data.nodeType` first, falling back to React Flow's own `node.type`.
- **`ApiInputConfigV2` / `ApiInputTableV2` / `ApiInputColumnV2`** (`apiInputSchema.ts`) — the
  api_input wire shape: a list of frame tables, each with a JSONPath `path`, a `label`, an
  `emit` flag, an optional `row_id_column`, and a list of columns (`name`, `path`, `type`,
  `status: "Confirmed"|"Inferred"`, `selected`, `levels?`, `origin?: "inferred"|"inherited"|
  "manual"`, `key?`).
- **`OutputMappingEntryV2`** (`outputMappingSchema.ts`) — `{ source_port, source_column,
  output_path, enabled }`; `OutputConfigV2 = { outputMapping: OutputMappingEntryV2[],
  outputFormat }`. `source_port` is the edge's `sourceHandle` (multi-table api_input) or
  `sanitizeName(sourceNode.label)` — mirroring the backend's port-key derivation.
- **`BandingFactor`** (`types/banding.ts`) — `{ banding: "continuous"|"categorical"|
  "breakpoints", column, outputColumn, rules, default?, rightClosed?, _prevRules? }`; rule shape
  varies by `banding` (`ContinuousRule` two-bound operator pair, `CategoricalRule` value+
  assignment, `BreakpointRule` boundary+label).
- **`RatingTable`** (`rating/ratingTableUtils.ts`) — `{ name, factors: string[] (≤3),
  outputColumn, defaultValue: string|null, entries: Record<string,string|number>[] }`, where
  `entries` is the (rebuildable) Cartesian product of the factors' known levels.
- **`GraphContextValue`** (`useGraph.ts`) — `{ allNodes, edges, submodels?, preamble? }`, provided
  by `<GraphProvider>` and required (throws otherwise) by `useGraph()`.

## Control flow

1. **Selection → dispatch.** `App`/the canvas passes the selected `SimpleNode | null` into
   `NodePanel`. `NodePanel` computes `config` (from `node.data.config`), `nodeType`
   (`effectiveNodeType`), `isKnownNodeType`, `isInstance` (`!!config.instanceOf`), and — before any
   early return, to satisfy React's hook-ordering rule — `nodeMap`, `upstreamEdges`,
   `inputSources`, `upstreamColumns`, `hasApiInputUpstream` (all memoised on signature strings
   derived from the upstream edges, not on `nodeMap` identity, so unrelated node edits don't churn
   these arrays for the selected node's own editor).
2. **Editor selection precedence** (`NodePanel.renderEditor`): unknown type → `UnknownNodeType
   Diagnostic`; else instance → `InstancePanel`; else a `switch (nodeType)` dispatching to the
   matching lazy editor from `LazyNodeEditors.tsx`, each wrapped by the caller in
   `LazyEditorBoundary`.
3. **Tab bar.** `showColumnsTab` is true only for known, non-instance types not in
   `NO_COLUMNS_TAB` (api_input, output, submodel, submodel port, modelling, explore). When
   `activeTab === "columns"`, `NodePanel` renders `GroupedColumnsTab` for `API_INPUT` and
   `ColumnsTab` for everything else — but since API_INPUT is *also* always in `NO_COLUMNS_TAB`,
   `showColumnsTab` is unconditionally false for it, so that `GroupedColumnsTab` branch in
   `NodePanel` is unreachable in the live app (see Edge cases below).
4. **Mutation.** Every editor calls its `onUpdate`/`onDeleteInput` props, never touches
   `node.data` directly. `handleConfigUpdate` (in `NodePanel`) reads the latest config/node via
   refs (not closed-over state) so it never captures a stale value across re-renders, merges the
   update into a new config object, strips `CACHED_PREVIEW_KEYS` (`_columns`,
   `_availableColumns`, `_schemaWarnings`) via `clearCachedResultShape`, and calls
   `onUpdateNode(node.id, newData)` — one call per commit gesture.
5. **Graph-context editors.** `SinkEditor`, `EdgeJoinEditor`, `RatingStepEditor` (factor-level
   extraction from connected banding nodes), and `InstancePanel` call `useGraph()` directly
   instead of receiving graph data as props — `NodePanel.graphContext.test.tsx` pins that these
   do *not* receive `allNodes`/`edges`/`submodels`/`preamble` as props.
6. **Instance resolution.** `resolveInstanceOriginal` searches the visible node map first, then
   every submodel's metadata graph (`submodelGraphFromMetadata`), collecting all matches; zero
   matches → `missing`, more than one → `ambiguous`, malformed submodel metadata → 
   `malformedSubmodel`, non-string/empty `instanceOf` → `invalid`. Only a single unambiguous
   match renders the real `InstancePanel` body; every other outcome renders
   `InstanceReferenceDiagnostic`. `resolveOriginalInputNames` + an auto-mapping heuristic
   (exact name match, then unambiguous substring match, then positional fallback) pre-fills
   `config.inputMapping`, leaving genuinely ambiguous pairings unmapped and flagged.
7. **API-input inference/reconciliation loop.** `ApiInputEditor` calls the backend's infer
   endpoint; the first inference on an empty config applies immediately via `writeBack`. Any
   later inference is staged as `pendingInferred` behind a confirm banner; confirming calls
   `reconcileInferredTables` (per-frame: user metadata kept, columns kept if confirmed /
   structurally blank / still present in the fresh inference, new inferred columns appended);
   invoking any other key action (cascade/inherit/attributes) while the banner is open implicitly
   dismisses it (treated as "keep my tables").
8. **Output-mapping conflict/preview loop.** `OutputEditor` derives one `FrameBlock` per incoming
   edge, keyed by `framePortId` (edge `sourceHandle` or `sanitizeName(sourceLabel)`, never `""`).
   On every render it recomputes `detectConflicts` over enabled rows (frontend best-effort mirror
   of the backend's per-port injectivity check) and renders `JsonPreview` dry-runs of both the
   whole assembled output and, lazily per-frame, the frame's raw input data.
9. **Edge-join analysis and save-time gate.** `EdgeJoinEditor.tsx` calls
   `edgeJoinValidation.analyzeEdgeJoinNode` on every render with the node's config plus the
   whole-graph `allNodes`/`edges` from `useGraph()`; the returned `diagnostics` array drives the
   editor's inline warning banner, and the resolved `baseColumns`/`joinColumns`/`commonColumns`
   drive its key pickers. Independently, `hooks/usePipelineAPI.ts`'s save path calls
   `findFirstInvalidEdgeJoin(allNodes, allEdges)` before every save and blocks with a toast
   (`formatEdgeJoinValidationIssue`) naming the first offending node and its first diagnostic —
   the same analysis function backs both the live editor feedback and the save-time hard stop.
10. **Key-pick dialogs.** `ApiInputEditor.tsx` is the sole caller of `KeyPickerModal`: it builds
    `InheritGroup[]` candidate lists (from `apiInputInherit.ts`) for the inherit, inherit-
    attributes, and cascade key-pick flows and reuses the same dialog component for all three,
    varying only the title, target label, accent colour, and what `onConfirm`/the optional
    `manualEntry.onAdd` do with the selected paths.
11. **Lazy loading.** `LazyNodeEditors.tsx` is the only file that calls `React.lazy(() =>
    import(...))` for editor bodies; `NodePanel` and `ReadOnlyNodeConfig` both import only from
    this module, never from `panels/editors/*` directly, so editor bodies stay behind a dynamic
    import boundary (enforced by `NodePanel.lazyEditors.test.ts`).

## Edge cases and invariants

- **Undo-atomicity.** Every text/code/cell input in this component buffers keystrokes locally and
  commits once per gesture (blur, or Enter for single-line fields) — never on every `onChange`.
  This is the single most-repeated design invariant across the module (`CommittedTextField`,
  `CodeMirrorEditor`'s debounced sync, `BandingRulesGrid`/`BreakpointGrid`'s cell inputs,
  `ControlledNumberCell`, `ScenarioExpanderEditor`'s custom min/max draft state, the API-input and
  output-mapping path inputs).
- **Render-gate invariant (never drop persisted-but-invalid data on read).** `apiInputSchema
  .readV2` and `outputMappingSchema.readV2`-equivalent logic keep structurally incomplete
  entries (blank name/path/label) visible and editable by default; `dropIncomplete: true` is used
  only for fresh inference output, never for reading persisted config.
- **Positional list keys.** API-input table/column rows and rating/banding grid rows are keyed by
  array index, not by content, specifically to avoid remount-on-edit focus loss; this is a
  documented trade-off against key stability under concurrent structural edits, not an oversight.
- **Confirm-on-use for API-input keys.** Using a path as a cascade/inherit/attributes key marks
  every column carrying that path — including the shallower source column — `Confirmed` and
  `key: true`, so a later re-infer can't silently drop the shallow original while broadcast
  copies of it persist deeper in the tree.
- **`removedTables` is a deliberately unimplemented field.** It was speced as a "don't resurrect
  deleted tables on re-infer" ledger but was never wired into inference; it's actively sanitised
  out of both `readV2` and `writeV2` rather than left half-implemented, and this is pinned by a
  dedicated sanitisation test.
- **`GroupedColumnsTab` is effectively unreachable from `NodePanel` today.** `NODE_TYPES.API_INPUT`
  is in `NO_COLUMNS_TAB`, which forces `showColumnsTab = false` and hides the Config/Columns tab
  bar entirely for api_input nodes — so the `nodeType === NODE_TYPES.API_INPUT ?
  <GroupedColumnsTab/> : <ColumnsTab/>` branch in `NodePanel.tsx` can never actually render via
  the tab flow. `GroupedColumnsTab` is only exercised through its own unit tests. This looks like
  a stale wiring decision rather than intended behaviour.
  > NOTE: this is a discrepancy between the apparent intent (grouped columns for api_input) and
  > the current dispatch logic, not a runtime bug — nothing crashes, the branch is simply dead.
- **Breakpoint ordering warnings are advisory UI feedback, not a save-time gate.**
  `bandingUtils.ts` detects overlapping ranges, gaps between ranges, and duplicate categorical
  values and surfaces them as inline warnings in `BreakpointGrid`/`BandingRulesGrid`; these never
  block saving or change what gets written to config — the Python/JSON config stays the sole
  source of truth for execution, and an out-of-order breakpoint set still runs (through
  `_breakpoints_to_rules` in the [rating](../rating/high-level.md) component) exactly as
  configured. This is a deliberate, narrower exception to the codebase's fail-loud default than
  the structural cases `_breakpoints_to_rules` itself does reject (duplicate boundaries, more
  than one open-ended breakpoint) — see [rating](../rating/low-level.md#edge-cases-and-invariants).
- **Banding's "continuous" rule type has no UI entry point.** `BandingEditor`'s type toggle only
  offers "Breakpoints" and "Categorical"; `"continuous"` (raw operator-pair rules) is fully
  modelled, validated, and rendered by `BandingRulesGrid` when present, but is reachable only as
  the default shape of a brand-new factor or via pre-existing/legacy config — never by direct
  user selection.
  > NOTE: continuous banding is load-bearing (default state, full validation support) but cannot
  > be chosen from the UI once a factor has been switched away from it.
- **`GenerateBandsDialog` label format assumes right-closed intervals** even though `rightClosed`
  isn't threaded into it — a left-closed factor can get a boundary label ("29–35") that doesn't
  match its actual `[29,35)` semantics.
- **`framePortId` fallback history.** `OutputEditor` explicitly documents a fixed bug where a
  null-handle frame's port used to fall back to `""`, collapsing two distinct single-frame
  sources into one port and tripping a schema error; the residual "two different frames still
  resolve to the same port" case is now surfaced via a duplicate-port banner rather than crashing.
- **Multi-frame preview fallback is approximate, not hidden.** If an output frame's edge
  `sourceHandle` doesn't match any emit-true table on a multi-frame source ("dangling handle"),
  the backend degrades to the first frame's rows for preview purposes and the UI shows an
  explicit caveat rather than silently rendering data as if it were correct.
- **`buildCartesianEntries` (rating tables) refuses to truncate to empty.** If any factor
  currently has zero known levels (e.g. preview data is transiently unavailable), the rebuild
  bails and returns the existing entries unchanged rather than wiping saved rate data.
- **`extractPreviewCategoricalLevels` excludes non-string columns wholesale** — any non-string,
  non-null value anywhere in a preview column disqualifies that whole column from being offered
  as a categorical rating factor, rather than coercing values.
- **Rating factor levels merge three sources with banding taking precedence.**
  `RatingStepEditor` builds each factor's dropdown/grid levels from, in order: banding-node
  output levels (`extractBandingLevels`), raw string/categorical preview columns
  (`extractPreviewCategoricalLevels`), and levels already present in the table's own saved
  `entries` — the latter two are filtered to exclude any column name already covered by a
  banding output (`onlyNonBandedLevels`) before merging, so a raw upstream column such as
  `channel` or `cover_type` is ratable directly without a banding step, while a banded column's
  levels always come from its banding rules. There is no separate raw-level editing UI for
  unbanded factors — preview data and saved entries are the only source.
- **Rating grid cells use neutral styling, not value-based heatmap colouring.** The editable
  relativity input (`EDITABLE_RELATIVITY_INPUT_STYLE` in `rating/cellStyles.ts`) is a plain
  bordered input with no background colour keyed to the value; `relativityColor`/
  `relativityTextColor` (`ratingTableUtils.ts`) compute a deviation-from-1.0 heatmap colour but
  are consumed only by `StatsFooter`'s min/max/avg summary, not by the editable cells
  themselves — keeping the spreadsheet-editing surface visually neutral while still surfacing
  outlier relativities in the read-only summary row.
- **Column pickers fall back to free text.** Any `<select>` driven by `upstreamColumns` renders as
  a plain `CommittedTextField` when no columns are known yet, everywhere this pattern occurs
  (edge-join keys, banding column pickers, rating factor pickers).
- **A value persisted but no longer present in a live list is retained, not dropped**, across
  multiple editors: `CatalogTablePicker`'s stale table option, `RegisteredModelPicker`'s stale
  model/version option, `EdgeJoinEditor`'s "Invalid join type"/"Missing column" synthetic options
  — the UI never silently substitutes or clears a value it didn't validate itself.
- **Explore's tab set is partially scaffolded.** `EXPLORE_PANES` lists five tabs; only Code and
  Overview have a rendering branch in `NodePanel` — Relationships/Charts/Export render as empty
  tab panels with no config component, by explicit design ("upcoming EDA work"), not by omission.
- **Edge-join role vs. config are cross-checked, not merged.** `analyzeEdgeJoinNode` treats "which
  node is wired to the base/join handle" (from the live edges) and "what `baseInput`/`joinInput`
  say in config" as two independent facts and diagnoses every way they can disagree: a role handle
  with zero or more than one incoming edge, a `baseInput`/`joinInput` value that isn't currently
  connected at all, and a `baseInput`/`joinInput` value that's connected but to the *other* role's
  handle. `baseColumns`/`joinColumns`/`commonColumns` prefer the role-derived input
  (`baseRoleInput`/`joinRoleInput`) over the config value when they differ, so the column pickers
  reflect what's actually wired even while the mismatch diagnostic is showing.
- **`swapEdgeJoinInputs` requires exactly one edge per role and refuses to fall back to config.**
  Zero or more than one incoming edge on either the base or join handle fails with a named reason
  (`base-input-not-found` / `base-input-ambiguous` / etc.) rather than swapping based on the node's
  `baseInput`/`joinInput` config values, which could silently diverge from what's actually wired.
- **`insertEdgeJoinNode`/`insertEdgeJoinNodeFromSources` reject self-joins and cycles before
  returning.** Connecting a node to itself as base+join, or creating an edge-join whose insertion
  would close a directed cycle in the whole graph (`hasDirectedCycle`, a DFS over the
  post-insertion edge set), both fail with a named reason instead of producing an invalid graph
  that would only be caught later.
- **A banding node's ambiguous auto-source is silent, not diagnosed.** `bandingLevelOrderForOptimiser`
  falls back to a directly-connected banding input only when there is exactly one
  (`singleDirectBandingInputId`); zero or more than one directly-connected banding node (with no
  explicit `banding_source` configured) returns `{}` — an empty level order — with no diagnostic
  distinguishing "no banding upstream" from "ambiguous banding upstream," unlike the edge-join and
  instance-resolution code paths elsewhere in this component, which do distinguish those cases.
- **`KeyPickerModal`'s manual-entry "Add" is context-sensitive on whether the path already exists.**
  A brand-new path requires both a non-empty, grammar-valid path *and* a chosen type before "Add"
  enables; a path that already exists on the target frame (per `existingPaths`) is addable with no
  type chosen at all — the type select is disabled and the existing column's name/type are kept
  unchanged, with `onAdd(path, null)` signalling "promote/re-confirm this existing entry" rather
  than "create a new typed column."

## Error handling

- `useGraph()` throws a descriptive `Error` (naming `<GraphProvider>` and the expected props) when
  called with no provider in the tree — this propagates to the nearest error boundary rather than
  being caught locally.
- File/network operations in editors (`FileBrowser`, `WarehousePicker`, `CatalogTablePicker`,
  `DatabricksFetchButton`, `MlflowStatusBadge`, `SinkEditor`'s write action, API-input's cache
  fetch) catch their promise rejections locally and render an inline error string or a disabled
  control with a reason tooltip; none let a rejected fetch bubble into a render-time throw.
- `ArtifactMetaPanel`/`OptimiserApplyEditor`'s file-mode `readJson` and similar artifact-parsing
  paths surface parse failures as inline error text next to the picker, not as a thrown exception.
- Invalid pasted/typed values (JSON-array records field, output/api-input path grammar, rating
  grid numeric paste) are rejected at the commit boundary: the field keeps its last valid
  committed value, shows an inline error, and never calls `onUpdate` with the invalid draft. A
  whole-paste operation that finds one invalid cell aborts the entire paste (no partial apply) and
  names the offending cell in a toast.
- `clipboardWriteAvailable()`/`downloadTextFile()` (`tableClipboard.ts`) return `false` rather than
  throwing when the Clipboard/Blob/URL APIs are unavailable; `FrameTableActions`/`JsonPreview`
  disable the corresponding button with a tooltip instead of letting the click throw. A distinct
  case is an *available* clipboard write that itself rejects at call time (e.g. a denied
  permission) — the rating grid's visible-table copy (`TwoWayGrid.copyVisibleTable`) catches this
  and surfaces it as an error toast naming the underlying `Error` message, rather than swallowing
  it or letting it become an unhandled promise rejection.
- Unknown node types and broken instance references never throw during render — they route to a
  dedicated diagnostic component (`UnknownNodeTypeDiagnostic`, `InstanceReferenceDiagnostic`) that
  dumps the raw offending config so the user (or a bug report) has the actual data, not a stack
  trace.
- `edgeJoinGraph.ts`'s three mutation functions and `edgeJoinValidation.ts`'s `analyzeEdgeJoinNode`
  never throw for malformed input: unexpected config shapes accumulate as diagnostic strings
  (`edgeJoinValidation`'s `read*` helpers coerce and push a message rather than throwing on a
  wrong-typed `on`/`leftOn`/`rightOn`/`coalesce`/`how`) or surface as a discriminated failure
  result (`edgeJoinGraph`'s `{ ok: false, reason }`), consistent with this component's general
  preference for named, typed failure states over exceptions in pure logic modules.

## Testing

Tests for this component live in two places: alongside their source under
`panels/editors/__tests__/`, `panels/editors/banding/__tests__/`, `panels/editors/rating/__tests__/`,
`panels/__tests__/`, `components/__tests__/`, `components/form/__tests__/`; and a second, larger
tree for the api-input/output-mapping areas under `frontend/src/__tests__/editors/`. All are
Vitest + React Testing Library component/unit tests; no end-to-end browser tests were found for
this component.

**Panel shell / dispatch** (`panels/__tests__/`):
- `NodePanel.test.tsx` — the largest suite: dispatch to every node type's editor, the Config/
  Columns tab bar, cached-column clearing on config change, dimmed-opacity prop, panel resize
  (drag clamps to 320px min / 75% max), `handleConfigUpdate` staleness-safety across re-renders,
  `previewRows`/upstream-columns wiring per editor, the full instance-resolution matrix (found,
  missing, ambiguous, malformed submodel, invalid instanceOf, hidden-submodel resolution, duplicate
  ids across submodels), input-mapping auto-fill/ambiguity, unknown-node-type and unknown-instance
  diagnostics, Explore pane switching (including per-node pane memory), submodel/submodel-port
  refresh-preview suppression.
- `NodePanel.graphContext.test.tsx` — regression coverage that graph-context-consuming editors
  (`SinkEditor`, `ModellingConfig`, `OptimiserConfig`) receive *no* graph props, and that a
  missing `instanceOf` fails loud with a named diagnostic rather than falling back to the
  stringified id.
- `NodePanel.lazyEditors.test.ts` — static-analysis-style guard that editor bodies are never
  statically imported and stay behind `React.lazy`/dynamic-import boundaries; utility panels stay
  off the editor-barrel runtime path.
- `LazyNodeEditors.accessibility.test.tsx` — the loading fallback announces via
  `role="status"`/`aria-live`.
- `PanelShell.test.tsx` / `PanelHeader.test.tsx` — width defaults/clamping, drag-resize, slide-in
  class, header title/subtitle/icon/actions/close rendering.
- `NodePalette.test.tsx` — template rendering, singleton-type disabling once present in the graph,
  drag-start payload and `effectAllowed`, disabled items are non-draggable and set no drag data.
- `configPanelsDry.test.ts` — a DRY guard asserting `ModellingConfig`/`OptimiserConfig` don't
  re-inline shared estimate/polling/hash logic that should come from a shared hook.
- `components/__tests__/ReadOnlyNodeConfig.test.tsx` — every known node type renders read-only
  without crashing; unknown type falls back to the JSON config dump.
- `components/form/__tests__/CommittedTextField.test.tsx` — the commit-on-blur/Enter contract
  directly: no commit while typing, one commit on blur/Enter, no-op commits skipped, a stale draft
  is dropped when the committed value changes underneath it (undo mid-edit), caller-supplied
  `onBlur`/`onKeyDown` still fire.

**API input** (`frontend/src/__tests__/editors/`):
`apiInputBundle3aContract.test.tsx` (Columns tab hidden + sanitised-label `configPath`),
`apiInputBundle3bContract.test.tsx` / `apiInputBundle3bCacheContract.test.tsx` (stale-columns
banner semantics; cache button DOM position), `apiInputBundle3cContract.test.tsx` (handle-list
node-body rendering + `useUpdateNodeInternals` signature-scoped firing), `apiInputPathGrammar
.test.tsx` (path grammar + non-canonical highlighting), `apiInputInherit.test.ts` (pure-logic unit
tests for the inventory/inherit/cascade/naming/dedup helpers), `apiInputInheritIntegration
.test.tsx` (large end-to-end interaction suite over the real editor: pickers, confirm gates,
re-infer reconciliation, key toggling, hand entry, salting), `apiInputSchemaReadGate.test.ts`
(the render-gate contract on `readV2`/`writeV2`), `apiInputSchemaSanitisation.test.ts`
(`removedTables` never resurrected), `panels/editors/__tests__/_DatabricksSelector.test.tsx`
(warehouse/catalog pickers, fetch-button lifecycle incl. polling/formatting/error/cleanup).

**Output mapping**: `OutputEditor.test.tsx` (~64 cases: frame rendering, row CRUD, v1→v2
migration, Infer/Inferred-pill lifecycle across removal, path validation + conflict detection,
non-canonical highlighting, duplicate-port collision, both JSON previews), `OutputEditorPathTools
.test.tsx` (header-prefix edit affordance, wired `FrameTableActions`), `jsonpath.test.ts` (grammar
acceptance/rejection, explicitly framed as backend-parity), `pathCanonicalWarning.test.tsx`,
`FrameTableActions.test.tsx` / `tableActionsHelpers.test.ts`.

**Banding**: `banding/__tests__/bandingUtils.test.ts` (interval/overlap/gap/duplicate/breakpoint
math), `BandingHistogram.test.tsx`, `BandingRulesGrid.test.tsx` (~50 cases incl. TSV paste in
multiple column-count shapes), `BreakpointGrid.test.tsx`, `CategoricalValuePicker.test.tsx`,
`GenerateBandsDialog.test.tsx`, and top-level `BandingEditor.test.tsx` (~55 cases: tabs, type-
switch stash/restore, ARIA tab wiring, match counts, validation warnings).

**Rating**: `rating/__tests__/OneWayEditor.test.tsx`, `TwoWayGrid.test.tsx` (~25 cases incl.
labelled/unlabelled TSV paste, invalid-cell toast naming the exact factor/level, drag-select copy
order), `StatsFooter.test.tsx`, plus top-level `ratingTableUtils.test.ts` and
`RatingStepEditor.test.tsx` (~55 cases: table search/filter/status, legacy combined-output
migration, per-node remembered section, 1/2/3-factor routing, factor-level source precedence).
`frontend/package.json`'s per-file coverage-threshold list singles out
`src/panels/editors/rating/ratingTableUtils.ts` for a stricter gate than the project default
(100% statements, 92% branches) — the one frontend file in this component held to an explicit
higher bar rather than the global threshold.

**Other per-node editors**: `ColumnsTab.test.tsx`, `GroupedColumnsTab.gaps.test.tsx`,
`ModelScoreEditor.test.tsx`, `MlflowModelPicker.test.tsx`, `OptimiserApplyEditor.test.tsx` (44
cases, the ratebook-input-selector visibility/staleness matrix across all three source types),
`ScenarioExpanderEditor.test.tsx` (heavy emphasis on the min/max draft-buffering undo-atomicity
contract), `ExploreCodeEditor.test.tsx`, `ExploreOverviewConfig.test.tsx`, `DataInputEditor.test
.tsx` / `DataOutputEditor.test.tsx` (registry-driven IO editor — unrelated to the `output` node's
`OutputEditor.tsx` despite the similar name), `CodeMirrorEditor.test.tsx`, `_shared.test.tsx`.

**Edge join**: `frontend/src/utils/__tests__/edgeJoinGraph.test.ts` (`insertEdgeJoinNode` splitting
an edge incl. repeated/chained splits and downstream role/fan-in-contract rewriting,
`insertEdgeJoinNodeFromSources` building an unconnected edge-join, `swapEdgeJoinInputs` swapping
config+handles and its ambiguous/missing-role rejections, self-join/cycle rejection) and
`frontend/src/utils/__tests__/edgeJoinValidation.test.ts` (`analyzeEdgeJoinNode`'s accept/diagnose
matrix — same-name vs. paired keys, cross-join key rejection, role/config mismatches, unknown
column keys — plus `findFirstInvalidEdgeJoin`/`formatEdgeJoinValidationIssue`). `edgeJoinRoles.ts`
has no dedicated unit test of its own; its handle-canonicalisation (incl. the legacy `join-bottom`
alias) is exercised only indirectly, through `edgeJoinGraph.test.ts`/`edgeJoinValidation.test.ts`
using its exported constants and through canvas-side tests
(`nodes/__tests__/PipelineNode.test.tsx`, `hooks/__tests__/useEdgeHandlers.test.ts`) that live
outside this component's own test tree — a genuine coverage gap for this component specifically,
even though the behaviour itself is tested elsewhere.

**Key picker / banding utility**: `components/__tests__/KeyPickerModal.test.tsx` (collapsible
grouped candidate rendering, already-present paths shown checked+disabled, confirm returning the
selected paths). The optional `manualEntry` "enter a field by hand" section — the
existing-path/type-required `addable` gating, `pathError` display, and the exists/already-a-key
notice text — has no test coverage in this file at all; every test in it omits the `manualEntry`
prop, so that whole branch (load-bearing for `ApiInputEditor`'s inherit-attributes flow) is
untested at the component level. `frontend/src/__tests__/utils/banding.test.ts`
(`extractBandingLevelsForNode`/`extractBandingLevels`/`extractBandingLevelOrderForNode` across
non-banding nodes, missing/empty factors, categorical/breakpoint/continuous rule shapes, and
default-value inclusion) — `bandingLevelOrderForOptimiser`'s explicit-`banding_source` vs.
single-direct-input vs. ambiguous-input fallback matrix is not exercised in this file; it is
covered, if at all, from the Optimiser component's own tests.

`panels/editors/rating/cellStyles.ts` has no dedicated test — it is two plain `CSSProperties`
constant objects with no branching logic, exercised only incidentally by rendering assertions in
`OneWayEditor.test.tsx`/`TwoWayGrid.test.tsx`.

**Known coverage note.** No test file's comments claim a deliberately-untested area for this
component (the `*.gaps.test.tsx` naming denotes "coverage added for a gap found in review," not
an open gap). The one functional discrepancy identified during this review —
`GroupedColumnsTab` being unreachable from `NodePanel`'s live dispatch — is covered only by
`GroupedColumnsTab.gaps.test.tsx` in isolation; there is no test asserting (either way) whether
`NodePanel` actually renders it for an api_input node's Columns tab, consistent with that branch
currently being dead. Three further gaps were found while extending this document: `edgeJoinRoles.ts`
has no test file of its own (see Edge join above); `bandingLevelOrderForOptimiser`'s
source-resolution branches are untested from this component's side; and `KeyPickerModal`'s
`manualEntry` section is entirely untested (see Key picker above).
