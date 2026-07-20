# Frontend Node Editors — Low-Level Specification

## Module map

| File | Responsibility |
| --- | --- |
| `frontend/src/panels/NodePanel.tsx` | Selects configuration/columns/instance views and routes the selected node to an editor. |
| `frontend/src/panels/NodePalette.tsx` | Renders draggable node templates. |
| `frontend/src/panels/LazyNodeEditors.tsx` | Central dynamic-import registry and loading boundaries for editor bodies. |
| `frontend/src/panels/PanelShell.tsx`, `frontend/src/panels/PanelHeader.tsx` | Right-panel shell/header used by node, imports and utility authoring views. |
| `frontend/src/components/ReadOnlyNodeConfig.tsx`, `frontend/src/components/FramesTable.tsx`, `frontend/src/components/KeyPickerModal.tsx` | Inert configuration, API-frame rows and reusable API-input key picker. |
| `frontend/src/panels/editors/index.ts` | Public editor exports. |
| `frontend/src/panels/editors/_shared.tsx` | Shared editor types, styles, file browser, schema preview and input-source bar. |
| `frontend/src/panels/editors/CodeEditor.tsx`, `frontend/src/panels/editors/CodeMirrorEditor.tsx`, `frontend/src/panels/editors/shared/PolarsCodePanel.tsx` | Code-editor wrappers and Polars-specific panel. |
| `frontend/src/panels/editors/ConstantEditor.tsx`, `frontend/src/panels/editors/TransformEditor.tsx`, `frontend/src/panels/editors/EdgeJoinEditor.tsx`, `frontend/src/panels/editors/LiveSwitchEditor.tsx`, `frontend/src/panels/editors/ScenarioExpanderEditor.tsx` | Editors for scalar, transform, join, conditional-switch and scenario nodes. |
| `frontend/src/panels/editors/DataSourceEditor.tsx`, `frontend/src/panels/editors/ExternalFileEditor.tsx`, `frontend/src/panels/editors/DataInputEditor.tsx`, `frontend/src/panels/editors/DataOutputEditor.tsx`, `frontend/src/panels/editors/SinkEditor.tsx` | File/database/IO source and destination configuration. |
| `frontend/src/panels/editors/_IoFormatEditor.tsx`, `frontend/src/panels/editors/_ioFormats.ts`, `frontend/src/panels/editors/_DatabricksSelector.tsx` | Registry-driven IO arguments, cached capabilities and Databricks controls. |
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

Browser coverage for authoring flows is in `frontend/e2e/data-io-nodes.spec.ts`,
`frontend/e2e/migration/v1-to-v2-node-continuity.spec.ts`,
`frontend/e2e/persistence/api-input-render-gate.spec.ts`, and
`frontend/e2e/persistence/api-input-v2-native.spec.ts`.
