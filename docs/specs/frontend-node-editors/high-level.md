# Frontend Node Editors — High-Level Specification

## Purpose

Haute pipelines are authored as a graph of typed nodes on a canvas (see
[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)). Selecting a node opens a
right-hand side panel where the user configures that node — picking a source file, writing
Polars code, defining a rating table, mapping API-request fields, shaping the final response
JSON, and so on. This component is that side panel: the panel chrome, the node-type-to-editor
dispatch, and the ~25 per-node-type editor bodies themselves.

It exists because every node type has a materially different configuration surface (a data
source needs a file picker and SQL query; a rating step needs a spreadsheet-like factor table;
an output node needs a JSON-path mapping tree) while still needing to feel like one coherent
product — consistent labels, consistent commit-on-blur text fields, a consistent "connected
inputs" chip bar, consistent read-only diagnostics when something is broken. The component is
also where several cross-cutting authoring problems are solved once, centrally, rather than once
per editor: keystroke-vs-undo-step granularity, JSON-path grammar validation shared between the
API-input and output editors, and lazy-loading the (large) editor bodies so the canvas and
palette stay fast to load.

## Scope

In scope:
- The side-panel shell and header chrome (width/resize, slide-in, title/close/actions) shared by
  every right-side panel in the app, not just the node panel.
- Node-type dispatch: given a selected node, choosing and rendering the correct editor, the
  generic "Columns" tab, the read-only "Instance of" panel for cloned nodes, and fail-loud
  diagnostics for unknown node types or broken instance references.
- The palette of draggable node templates used to add nodes to the canvas.
- Every per-node-type config editor body (`panels/editors/*`) and their shared sub-components
  (banding grid/histogram, rating one-way/two-way grids, IO-format registry editor, JSON path
  mapping tools, code editor wrapper, the `KeyPickerModal` dialog reused by the API-input editor's
  inherit/inherit-attributes/cascade flows).
- The generic form primitives (`components/form/*`) used across editors: commit-on-blur text
  field/area, checkbox, uppercase micro-label.
- A read-only variant of the same editor set, used to render a node's configuration inertly in
  comparison views.

Out of scope (owned by neighbouring components):
- The canvas itself — node placement, edges, drag/connect, minimap — owned by
  [frontend-graph-canvas](../frontend-graph-canvas/high-level.md). This component only supplies
  the palette's drag payload and consumes the canvas's selected node / graph context. The
  edge-join *graph-mutation* helpers (`utils/edgeJoinGraph.ts`: splitting an edge into a new
  edge-join node, swapping its base/join roles) live in this component's module map but are
  invoked exclusively from the canvas's own connection/context-menu handlers, not from any editor
  body — see the low-level spec's Module map note for the full boundary discussion.
- Generic UI atoms reused outside the node panel (`ToggleButtonGroup`, `ColumnTable`, toast/status
  primitives) — owned by [frontend-shared](../frontend-shared/high-level.md).
- All HTTP calls to the backend (file listing, cache fetch/status, Databricks catalogs, MLflow
  browsing, sink execution, IO-format capability registry) — owned by
  [server-api](../server-api/high-level.md); editors are thin consumers of that client.
- The JSON-path grammar itself (parsing, canonicalisation, the backend-mirrored construct rules)
  — the frontend copy lives inside this component (`panels/editors/jsonpath.ts`) for editor-local
  validation, but the authoritative grammar and its execution semantics are owned by
  [expression-parsing](../expression-parsing/high-level.md); this component only re-validates the
  same rules client-side to fail fast before a save round-trip.
- Preview execution, dry-runs, and the resulting row/column data the editors consume as props —
  owned by the pipeline execution layer that feeds `NodePanel`'s `previewRows` /
  `node.data._columns` inputs.
- The Modelling and Optimiser node editors' *internal* tabs (`ModellingConfig`,
  `OptimiserConfig`) are dispatched to from here but documented in their own specs (modelling,
  optimiser) since they are large enough to be separate components; this spec covers only how
  NodePanel routes to them.

## Behaviour

**Selecting a node opens one editor.** `NodePanel` receives the currently-selected node (or
`null`, in which case it renders nothing) and renders exactly one of: the type-specific editor,
a generic "Columns" tab (column selection/rename — most node types), the read-only "Instance of"
panel (for nodes cloned from another node/submodel original), or a fail-loud diagnostic (unknown
node type, or a broken instance reference). Which of these applies is derived purely from the
node's type and config — never guessed.

**Every field commits on blur or Enter, never per keystroke.** Typing into any label, path, code,
or table cell buffers locally; the underlying node config — and the undo stack — only advances
once per edit gesture, not once per character. This holds uniformly across plain text fields,
code editors, and grid cells.

**Connected inputs are always visible and removable.** Any editor whose node accepts upstream
connections shows a compact bar of the connected input names, each removable in place (dropping
the edge) without leaving the editor.

**Long-lived pipeline structure (API input, output mapping) never silently discards user data.**
Re-inferring an API input's schema from fresh data, or a config round-trip through disk, never
drops an entry the user confirmed or hand-entered — at worst it surfaces the entry as invalid
and lets the user repair or delete it explicitly.

**Column pickers degrade to free text.** Wherever an editor offers a dropdown of upstream/known
column names, if no columns are known yet (nothing has been previewed) the same field is a plain
text input instead of an empty, unusable dropdown.

**A broken reference explains itself instead of crashing.** An instance node whose original is
missing, ambiguous, or lives inside malformed submodel metadata renders a labelled diagnostic
with the raw offending config, not a blank panel or a thrown error. The same applies to a node
type the running UI build doesn't recognise.

**Editor bodies load lazily.** The first time a given node type is selected, its editor's code
is fetched on demand (with a loading placeholder) rather than being part of the initial bundle;
subsequent selections of the same type render instantly from the already-loaded module.

## Design rationale

**Commit-on-blur, not per-keystroke, for every text/code/cell input.** The node graph's undo
stack pushes one snapshot per config mutation. Wiring `onChange` straight through to config
update meant typing an N-character value produced N undo steps, so a single Ctrl-Z reverted one
character. `CommittedTextField`/`CommittedTextArea` (`components/form/CommittedTextField.tsx`)
and the equivalent buffering built directly into `CodeMirrorEditor`, `BandingRulesGrid`,
`ControlledNumberCell`, and the API-input/output path inputs all exist to collapse one user
gesture into one committed change. This is treated as a correctness property, not a nicety —
several editors have dedicated tests asserting it.

**A render-gate invariant for persisted-but-invalid data.** The API-input and output-mapping
editors both explicitly keep structurally incomplete entries (a table with a blank path, a
mapping row with no column chosen) visible and editable rather than silently dropping them on
read. The alternative — quietly filtering them out on load — was a real prior bug class: a
dropped-but-still-serialised entry is invisible to the user and gets permanently deleted the next
time the editor writes its config back to disk. `readV2`/`writeV2` in both
`panels/editors/apiInputSchema.ts` and `panels/editors/outputMappingSchema.ts` codify this as a
named contract, with `dropIncomplete` used only for genuinely fresh, unpersisted data (an
Infer-Tables response).

**Confirm-gated re-inference instead of clobber-on-refresh.** Re-running schema inference for an
API input node does not overwrite the user's curated tables outright; a first inference on an
empty node applies immediately, but any subsequent inference stages its result behind a
confirm/cancel banner and reconciles column-by-column (`apiInputInherit.ts:reconcileInferredTables`)
so hand-edited or explicitly-confirmed columns survive a re-infer that would otherwise have
proposed something different.

**Backend-mirrored, backend-authoritative validation.** Path grammars (`jsonpath.ts`), name
sanitisation, and label-collision rules are deliberately re-implemented client-side to fail fast
in the editor, but every one of these frontend checks documents which backend module it mirrors
(`_jsonpath.py`, `_config_io.py`'s `_sanitize_func_name`, `validate_v2_schema`) and treats the
backend as the source of truth — a best-effort frontend conflict detector
(`OutputEditor.tsx:detectConflicts`) explicitly says so in its own doc comment. The edge-join
node's handle IDs and config keys (`utils/edgeJoinRoles.ts`) are the same pattern applied to a
smaller surface: the file's own header comment says to keep them in sync with the backend's
`_edge_join` module. This avoids duplicated, drifting validation logic while still giving the
user immediate feedback instead of a save-time 422.

**Registry-driven IO editors instead of hard-coded format lists.** `DataInputEditor`/
`DataOutputEditor` render entirely from `GET /api/formats` (`_ioFormats.ts`) — format names,
modes, argument names, and "engine missing" flags are all server-supplied. Adding a new
polars-supported format requires no frontend change.

**Lazy loading via one indirection point.** Every editor body is dynamically imported through
`LazyNodeEditors.tsx` rather than imported directly by `NodePanel`/`ReadOnlyNodeConfig`. This
keeps the editor bodies (several of which are tens of kilobytes, e.g. `OutputEditor.tsx`,
`ApiInputEditor.tsx`) out of the initial bundle and is enforced by a dedicated test
(`NodePanel.lazyEditors.test.ts`) that fails if any editor is statically imported.

**Separate grid components for banding and rating despite shared clipboard logic.** Banding rules
(`BreakpointGrid`/`BandingRulesGrid`) and rating lookup tables (`OneWayEditor`/`TwoWayGrid`) are
editable grids with overlapping paste/copy needs, but are deliberately not unified behind one
renderer: banding rules are editable range/category *definitions*, while rating tables are
value *lookups* keyed on levels defined elsewhere (often by a banding node upstream). The two
share clipboard primitives (`shared/tableClipboard.ts`) but not a grid component, keeping each
editor's semantics direct rather than forcing both through a shape neither fits cleanly.

**Positional, not path-derived, list keys in editable grids.** Both the API-input table/column
rows and the rating/banding grids key their rows by array index rather than by content-derived
identity. Path- or name-derived keys caused the row to remount mid-edit whenever its own content
changed — losing focus on every keystroke — which is worse than the (rare) cost of index-based
keys reordering incorrectly under concurrent structural edits.

## Interactions

- **[frontend-graph-canvas](../frontend-graph-canvas/high-level.md)** — supplies the selected
  node, the full node/edge list, and graph mutation callbacks (`onUpdateNode`, `onDeleteEdge`,
  `onSwapEdgeJoinInputs`) that `NodePanel` calls into; consumes `NodePalette`'s drag payload
  (`application/reactflow-type` / `application/reactflow-config`) to create new nodes.
- **[frontend-shared](../frontend-shared/high-level.md)** — `ToggleButtonGroup`, `ColumnTable`,
  and other generic UI atoms are composed throughout the editors rather than reimplemented.
- **[server-api](../server-api/high-level.md)** — every network call from an editor (file
  listing, cache fetch/status, Databricks catalog/warehouse browsing, MLflow model/run browsing,
  sink execution, the IO-format capability registry) goes through the shared API client; editors
  hold no fetch logic of their own beyond calling it and rendering loading/error states.
- **[expression-parsing](../expression-parsing/high-level.md)** — the JSON-path grammar used by
  the API-input and output-mapping editors (`panels/editors/jsonpath.ts`) is a frontend mirror of
  the backend path grammar; this component treats the backend as authoritative and only
  early-validates the same rules.
- **Modelling / Optimiser components** — `NodePanel` dispatches `MODELLING` and `OPTIMISER` node
  types to `ModellingConfig`/`OptimiserConfig`, which are large enough to have their own specs;
  this component owns only the dispatch and the upstream-columns/`_nodeId` props passed in.
- **Pipeline execution / preview** — this component is a pure consumer of preview data
  (`previewRows`, `node.data._columns`, `node.data._availableColumns`,
  `node.data._schemaWarnings`) and of the `onRefreshPreview`/`selectedPreviewLoading` signals; it
  never fetches or computes preview data itself.
- **Comparison view** — `components/ReadOnlyNodeConfig.tsx` renders the same editor set with
  no-op handlers and an empty graph context, for showing "what this node's config looked like"
  without allowing interaction; it is a deliberately separate, parallel switch statement from
  `NodePanel.renderEditor` rather than a shared one, so the heavily-tested live `NodePanel` stays
  untouched by read-only concerns.
- **[frontend-assistant-ui](../frontend-assistant-ui/high-level.md)** — the assistant chat panel
  renders inside the shared side-panel shell this component owns and follows the same
  lazy-loading convention as the editor bodies; it consumes the shell only, none of the
  editor plumbing.

## Failure model

This codebase prefers loud failure over silent fallbacks, and the node-editor layer follows that
consistently:

- **`useGraph()` outside a `<GraphProvider>` throws**, rather than falling back to an empty graph
  — a mis-mounted editor surfaces in the error boundary immediately instead of silently rendering
  against no upstream data.
- **An unknown node type renders a labelled diagnostic** (`UnknownNodeTypeDiagnostic`) with the
  raw config dumped read-only, rather than rendering nothing or guessing an editor. The same
  pattern applies to a node whose `instanceOf` reference cannot be resolved
  (`InstanceReferenceDiagnostic`): invalid id, missing original, ambiguous original (found in
  more than one place), or malformed submodel metadata each get a distinct, named diagnosis
  rather than a generic "not found."
- **Ambiguous name-mapping is blocked, not guessed.** When an instance's input mapping can't be
  inferred unambiguously (a substring match fits more than one candidate), the UI leaves it
  unmapped and visibly flags it — the backend refuses to save or run an ambiguous mapping rather
  than accept a guessed pairing that could bind the wrong frames.
- **Unrecognised config keys are shown, not dropped.** The IO-format editor's
  `UnrecognisedKeysSection` and the API-input/output schema readers keep any config content the
  structured UI doesn't model, rather than silently discarding it on the next save.
- **Invalid values persist their previous committed state, not a corrupted one.** JSON-array
  fields, numeric grid cells, and path inputs all reject an invalid draft at the commit boundary
  and keep showing the last valid value (with visible error state), instead of committing
  malformed data upstream.
- **Unknown arguments in the IO-format editor are saved anyway.** A best-effort convenience for
  arguments not in the registry's known list: the value is kept and flagged, and execution is
  left to fail loudly server-side rather than the editor silently dropping a value the user
  typed.
- **Network/API failures in editors surface as inline error text or a disabled control with a
  reason tooltip** (file browser errors, MLflow status badge, Databricks pickers, cache-status
  fetch failures) — never a silently empty or silently stale UI.
