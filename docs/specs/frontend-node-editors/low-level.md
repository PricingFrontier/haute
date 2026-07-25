# Frontend Node Editors — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/NodePanel.tsx` | Selects configuration/columns/instance views, routes the selected node to an editor, and derives the per-edge `InputSource` list via `edgeInputName` (memoised on a per-edge signature covering edge id, source id/label, `sourceHandle`, the derived input name, and the `frameUnresolved` resolution state). |
| `frontend/src/panels/NodePalette.tsx` | Renders draggable node templates. |
| `frontend/src/panels/LazyNodeEditors.tsx` | Central dynamic-import registry and loading boundaries for editor bodies. |
| `frontend/src/panels/PanelShell.tsx`, `frontend/src/panels/PanelHeader.tsx` | Right-panel shell/header used by node, imports and utility authoring views. |
| `frontend/src/components/ReadOnlyNodeConfig.tsx`, `frontend/src/components/FramesTable.tsx`, `frontend/src/components/KeyPickerModal.tsx` | Inert configuration, API-frame rows and reusable API-input key picker. |
| `frontend/src/panels/editors/index.ts` | Public editor exports. |
| `frontend/src/panels/editors/_shared.tsx` | Shared editor types, styles, file browser, schema preview and the input-source bar (chips keyed by edge id, showing each edge's input name — the code argument — with the source node named in the tooltip). |
| `frontend/src/panels/editors/CodeEditor.tsx`, `frontend/src/panels/editors/CodeMirrorEditor.tsx`, `frontend/src/panels/editors/shared/PolarsCodePanel.tsx` | Code-editor wrappers and Polars-specific panel. |
| `frontend/src/panels/editors/ConstantEditor.tsx`, `frontend/src/panels/editors/TransformEditor.tsx`, `frontend/src/panels/editors/EdgeJoinEditor.tsx`, `frontend/src/panels/editors/LiveSwitchEditor.tsx`, `frontend/src/panels/editors/ScenarioExpanderEditor.tsx` | Editors for scalar, transform, join, conditional-switch and scenario nodes. `EdgeJoinEditor` exposes fixed canvas-derived base/join roles, atomic swap, the seven supported join modes, mutually exclusive same-name/asymmetric key forms, and advanced Polars options. |
| `frontend/src/panels/editors/ExternalFileEditor.tsx`, `frontend/src/panels/editors/DataInputEditor.tsx`, `frontend/src/panels/editors/DataOutputEditor.tsx` | External-object, grouped tabular input, and grouped tabular output configuration. |
| `frontend/src/stores/useOutputWriteStore.ts` | Per-node output-write request identity, pending/terminal lifecycle, and overwrite-confirmation state retained across editor remounts. |
| `frontend/src/panels/editors/_IoFormatEditor.tsx`, `frontend/src/panels/editors/_ioFormats.ts`, `frontend/src/panels/editors/_DatabricksSelector.tsx`, `frontend/src/panels/editors/_InputCacheControls.tsx` | Registry-driven IO arguments, cached capabilities, dedicated Databricks browsing, and shared input-cache lifecycle controls. |
| `frontend/src/panels/editors/ApiInputEditor.tsx`, `frontend/src/panels/editors/apiInputSchema.ts`, `frontend/src/panels/editors/apiInputInherit.ts`, `frontend/src/panels/editors/FrameTableActions.tsx` | API-input frame/schema editing, persisted/inferred schema conversion, reconciliation and row actions. |
| `frontend/src/panels/editors/OutputEditor.tsx`, `frontend/src/panels/editors/outputMappingSchema.ts`, `frontend/src/panels/editors/outputPathTools.ts`, `frontend/src/panels/editors/jsonpath.ts`, `frontend/src/panels/editors/pathCanonicalWarning.ts`, `frontend/src/panels/editors/JsonPreview.tsx` | Output mappings, JSON-path validation/rewrites, canonical-path hints and preview. |
| `frontend/src/panels/editors/ColumnsTab.tsx`, `frontend/src/panels/editors/GroupedColumnsTab.tsx` | Generic flat/grouped column configuration. |
| `frontend/src/panels/editors/ExploreCodeEditor.tsx`, `frontend/src/panels/editors/ExploreOverviewConfig.tsx` | Explore-code and overview-card configuration. |
| `frontend/src/panels/editors/MlflowModelPicker.tsx`, `frontend/src/panels/editors/ModelScoreEditor.tsx`, `frontend/src/panels/editors/OptimiserApplyEditor.tsx`, `frontend/src/panels/editors/SubmodelEditor.tsx` | MLflow/model-score, optimiser-apply and submodel editors. |
| `frontend/src/panels/editors/BandingEditor.tsx` | Composes banding mode, rules, histogram and generation controls. |
| `frontend/src/panels/editors/banding/index.ts`, `frontend/src/panels/editors/banding/bandingUtils.ts` | Banding public barrel and rule/level utility functions. |
| `frontend/src/panels/editors/banding/BreakpointGrid.tsx`, `frontend/src/panels/editors/banding/BandingRulesGrid.tsx`, `frontend/src/panels/editors/banding/CategoricalValuePicker.tsx` | Numeric breakpoints, editable rules and categorical selection. |
| `frontend/src/panels/editors/banding/BandingHistogram.tsx`, `frontend/src/panels/editors/banding/GenerateBandsDialog.tsx` | Histogram context and generated-band dialog. |
| `frontend/src/panels/editors/RatingStepEditor.tsx` | Rating-table and combined-output orchestration. |
| `frontend/src/panels/editors/rating/index.ts`, `frontend/src/panels/editors/rating/ratingTableUtils.ts`, `frontend/src/panels/editors/rating/cellStyles.ts` | Rating barrel, normalisation/levels/statistics/colours and cell styles. |
| `frontend/src/panels/editors/rating/OneWayEditor.tsx`, `frontend/src/panels/editors/rating/TwoWayGrid.tsx`, `frontend/src/panels/editors/rating/ControlledNumberCell.tsx`, `frontend/src/panels/editors/rating/StatsFooter.tsx` | One-/two-way editing, commit-on-blur number input and table statistics. |
| `frontend/src/panels/editors/shared/tableClipboard.ts` | Clipboard parsing/writing and TSV/CSV download helpers shared by editable grids. |
| `frontend/src/components/form/index.ts`, `frontend/src/components/form/CommittedTextField.tsx`, `frontend/src/components/form/ConfigCheckbox.tsx`, `frontend/src/components/form/EditorLabel.tsx` | Form barrel, committed text/area drafts, config checkboxes and accessible editor labels. |

## Key types and data structures

- `OnUpdateConfig`, `SimpleNode`, `SimpleEdge`, `SchemaInfo` and `InputSource` in
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
- API schemas have separate persisted read/write and inferred/reconciled representations in
  `frontend/src/panels/editors/apiInputSchema.ts` and `frontend/src/panels/editors/apiInputInherit.ts`.
  Output mappings use the equivalent conversion boundary in
  `frontend/src/panels/editors/outputMappingSchema.ts`.
- `RatingTable` and factor/entry structures are normalised by
  `frontend/src/panels/editors/rating/ratingTableUtils.ts`; banding grids consume and update the
  node's banding-rule records through `frontend/src/panels/editors/banding/bandingUtils.ts`.

## Control flow

1. `frontend/src/panels/NodePanel.tsx` receives selection and graph context, chooses an editor
   or generic tab, and passes config mutation callbacks and available preview/connection data.
   For each upstream edge it builds an `InputSource`: `name` via
   `edgeInputName(edge, sourceNode, submodels)` (from frontend-graph-canvas's
   `frontend/src/utils/apiInputPorts.ts`, mirroring the backend's `edge_input_name`). An
   API-input edge whose non-null `sourceHandle` names no currently-eligible frame — the only
   unresolved case the editor can transiently observe, since null-handle API edges are always
   pruned by reconciliation — keeps that handle **verbatim** as `name` and sets
   `frameUnresolved: true`, so the chip shows the stale frame identity with its warning state
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
   panel callback. Clipboard/drag/dialog operations remain local until that callback.
5. Format, file, catalog and MLflow controls issue their own API calls. Their request state is
   local to the editor; the editor never assumes an out-of-order response still describes a
   changed node unless its own effect/request guards accept it.
6. `EdgeJoinEditor` derives its two role displays from canonical `base`/`join` incoming handles
   and the matching `baseInput`/`joinInput` config. The swap callback is owned by the graph canvas
   because handles and config must move in one graph transaction. Selecting `cross` clears
   `on`/`leftOn`/`rightOn`; selecting another mode exposes either same-name rows (`on`) or paired
   rows (`leftOn`/`rightOn`) and each key-mode switch clears the inactive representation. The
   join type list is exactly `inner`, `left`, `right`, `full`, `semi`, `anti`, `cross`.

## Edge cases and invariants

- Column selectors accept unavailable/empty preview schema through their editor-specific text or
  persisted-value route; a known value is not silently erased just because upstream preview data
  changed.
- Schema/output rows that are persisted but incomplete stay editable. Fresh inferred rows can be
  filtered/merged before persistence without making existing user rows disappear.
- Rating table normalisation supports missing/malformed entries by producing the editable table
  contract; two-way grids keep their cartesian factor coordinates aligned with their entry values.
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
- `edgeInputName` treats only API-input sources' handles as frame names; a submodel
  `out__`-prefixed source handle resolves to the referenced child node's sanitised label (via
  the graph context's `submodels`) — the same name the flattened code binds — and every other
  node type derives the sanitised source label.
- The API-input editor rejects a frame label that fails backend invariant B4 (not an ASCII
  identifier, or a Python hard keyword) at commit time with the same inline validation used
  for blank/duplicate labels (`apiInputLabelIssue` — the exact ASCII mirror) — the label is
  the downstream argument name, so an invalid label never reaches the config.
- Null-handle API-input edges never survive reconciliation (the zero-frame default handle is
  non-connectable and `validSourceHandleKeys` for an apiInput never contains the empty key),
  so the only unresolved state the panel can observe is transient: an edge whose non-null
  `sourceHandle` names a frame that no longer resolves. Its chip keeps the handle text
  verbatim with the `frameUnresolved` warning; at run time the executor's `KeyError` is the
  loud backstop, and at save time codegen's port-less-edge `ParseError` covers hand-edited
  files.
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
`frontend/e2e/migration/v1-to-v2-node-continuity.spec.ts`,
`frontend/e2e/persistence/api-input-render-gate.spec.ts`,
`frontend/e2e/persistence/api-input-v2-native.spec.ts`, and
`frontend/e2e/persistence/api-input-frame-alignment.spec.ts` (downstream frame-naming chips
alongside its canvas geometry assertions).

## Approved change contract — 0.7.0 unified data I/O editors

Remaining node-editor improvement work is tracked in the
[frontend canvas roadmap](../../roadmap/frontend-canvas.md).

- Delete `frontend/src/panels/editors/DataSourceEditor.tsx` and
  `frontend/src/panels/editors/SinkEditor.tsx`, their lazy-registry entries, palette definitions,
  tests, fixtures, icons, and type guards. `DataInputEditor.tsx` becomes the provider/group
  orchestrator; `DataOutputEditor.tsx` receives `nodeId`/graph context and owns the explicit
  write action formerly isolated in `SinkEditor`.
- Replace the format-only payload helper with a guarded, cached
  `/api/io-capabilities` client. Types represent ordered groups, provider fields, per-direction
  formats/modes/arguments/engines, direct-batching, snapshot-build boundedness, native
  sink/eager-writer class, and publication modes. Rendering derives every option from that
  payload. `_IoFormatEditor.tsx` remains a shared registry-backed body but accepts one selected
  group and direction rather than flattening all formats.
- Add focused provider sections for file path browsing, database connection/query, lakehouse
  locator/options, Databricks selectors, and inline records. Add one shared source-snapshot
  component backed by the guarded input-cache API. Databricks reuses that component and retains
  `_DatabricksSelector.tsx` only for browsing.
- Add an atomic `replaceConfig(nextConfig)` editor callback alongside field updates. Input-type
  changes construct a fresh valid branch with only safe common presentation fields retained;
  they commit once and therefore produce one undo item. Capability/group/format inconsistency is
  rendered as an error, not repaired in an effect.
- `DataInputEditor` always mounts the shared Polars code panel beneath provider/cache controls.
  `DataOutputEditor` never mounts it. The output Write action sends the unsaved current graph,
  node id, active execution source, and streaming settings through the existing explicit sink
  request, and surfaces cancellation/admission/publication diagnostics.
- The one-to-one JSON/UI invariant remains: every key valid for the active branch has an editable
  or visible read-only representation; an invalid/inactive key is shown as a configuration
  error. The old generic “unrecognised keys, saved anyway” behaviour does not legitimise keys
  from another input branch.

Unit/component suites cover API guard rejection, ordered grouping, each field kind, dependency
messages, direct/snapshot constraints, all cache states, type-switch atomicity/undo, Polars code
round-trip, output direction filtering, write gating/status, and no output code panel.
`frontend/e2e/data-io-nodes.spec.ts` is rewritten for the hard-cutover graph and expanded with
provider grouping, snapshot refresh, cached offline execution, multiple format legs, atomic file
write, and removed-node absence. The legacy node-continuity migration suite is deleted rather
than adapted.

## I/O authoring feedback and output lifecycle

- `DataInputEditor` calls `useSchemaFetch` with the configured file path only
  when `format.input.needs_schema_when_bounded` is true. It imports
  `SchemaPreview`, renders the hook's loading/error states, and merges
  `Object.fromEntries(schema.columns.map(({name, dtype}) => ...))` into the
  current arguments on confirmation.
- `useSchemaFetch` recognises `ApiError` and stores `detail ?? message`.
  `ApiInputEditor` consumes and renders its `error` return.
- A small Zustand output-write store owns entries keyed by node id. Each entry
  carries request id, full request identity, phase
  (`writing | success | error | confirm_overwrite`), and structured result or
  message. Terminal actions update only the matching request id.
- `DataOutputEditor` asks `/api/pipeline/output-destination` for the display
  destination and extension mismatch; it does not reimplement backend path or
  default-extension rules. Stale destination responses cannot replace a newer
  request's state.
- The write identity covers the complete flattened graph, output node, active
  source, and streaming chunk size. Overwrite confirmation remains visible and
  actionable only while that whole request is unchanged. A graph or setting
  edit invalidates the grant before an `overwrite=true` retry.
- A node-level write remains mutually exclusive across config edits. While an
  older identity is still writing, the editor continues to show that pending
  state instead of presenting a disabled button with no explanation. Obsolete
  terminal entries are cleared when the editor observes a different request
  identity, and terminal state is removed when its editor unmounts so a deleted
  and recreated node id cannot inherit an earlier overwrite grant.
- The editor starts the API promise after recording store state; the promise
  updates the store even if the component unmounts. It sends `overwrite=false`,
  handles `ApiError.status === 409` as `confirm_overwrite`, and retries true
  only from the confirmation action.
- `WriteOutputArgs` adds `overwrite?: boolean` and serialises an explicit
  boolean. Tests reset the write store between cases and exercise unmount/
  remount while a deferred request is unresolved.
