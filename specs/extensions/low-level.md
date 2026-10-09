# Extensions — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_extensions.py` | Server side of extensions: `EXTENSIONS_GROUP`, `RESERVED_NAMES`, the frozen `Extension` record (with `api_base`, `assets_url` and `entry_url`), `ExtensionError`, `discover_extensions` (loads and validates every entry point in name order), `mount_extensions` (mounts each router and asset directory and registers `GET /api/extensions`), and `extension_package_dirs` (the package directories dev mode's reloader watches, found without importing the extensions). It also reads an extension's optional `quote_tables` function, of which at most one installed extension may have one, and beside it an optional `quote_sample` and an optional `response_tables`, and `mount_extensions` serves all three as `GET /api/quote-tables`: run in the thread pool, the tables and the response's tables each checked with `validate_v2_schema` and the sample served unchecked. |
| `src/haute/_workbench_output.py` | The Workbench Output's tables and response: `WorkbenchOutputError`, `workbench_output_tables` (its tables read from its config: checked with `validate_v2_schema`, each one-row or many-row by its path, each column at its table's level), `workbench_output_mapping` (its mapping read from its config, checked against the tables), `WorkbenchOutputTables` (its result: its tables' frames by label, carrying the tables they fill), `fill_workbench_tables` (the tables filled from the frames by port through the mapping, each column checked against and cast to its declared type, a one-row table checked for its one row when it is read), `workbench_response` (the response built from the tables by the Quote Response's assembler) and `as_response` (a response node's result as the frame a request is answered with). |
| `frontend/src/api/extensions.ts` | `fetchExtensions`: `GET /api/extensions` through the shared request machinery, validated by the generated `extensions` contract, whose validators load with the first response. `fetchQuoteTables`: `GET /api/quote-tables`, validated by the same group's `QuoteTablesResponse`. |
| `frontend/src/stores/useExtensionsStore.ts` | `useExtensionsStore` and `PIPELINE_VIEW`: the installed extensions, the active view (`"pipeline"` or an extension name), the toolbar element the active extension renders its controls into, and `viewSave`, the mounted extension's `save`. `saveView` is the toolbar's Save in an extension's view: when that view has a `save` it calls it, toasts "Saved <label>" or that it could not be saved, and returns true; otherwise it returns false and the editor saves the pipeline. `load` fetches the list, leaves the store untouched when none is installed (so the editor never re-renders for it) and reports a failure as one error toast; `showView` rejects a view that is not installed. It also holds `quoteTables`, the quote's tables from the newest fetch that succeeded, and `refreshQuoteTables`, which numbers its fetches so that only the newest publishes them or toasts its failure; `showView` starts one when the pipeline view shows after an extension's. |
| `frontend/src/utils/extensionQuoteTables.ts` | The copy rules for a Workbench Input and a Workbench Output: `QuoteTables`, `quoteTablesSupplier`, `responseTablesSupplier`, `quoteTablesPatch` (the update a Workbench Input's copy needs, `{tables, sample}`, both compared as JSON with object keys sorted and a missing sample read as `{}`, or null), `responseTablesPatch` (the update a Workbench Output's copy needs, `{tables}` compared the same way and `mapping` without the entries of tables and columns the new tables lack, or null), `paletteWorkbenchInputConfig` and `paletteWorkbenchOutputConfig` (what the palette's Workbench Input and Workbench Output start with: the newest tables, and sample, fetched), `workbenchOutputTableLabels` (a Workbench Output's ports, its tables' labels in order), and `WORKBENCH_COPY_PATCHES` (the update rule for each workbench node type, `quoteTablesPatch` for a Workbench Input and `responseTablesPatch` for a Workbench Output). |
| `frontend/src/hooks/useExtensionQuoteTables.ts` | The hook the editor shell (`frontend/src/App.tsx`) calls to keep those copies current: a fetch when a supplier is listed and on each `executionGeneration`, recorded against the generation; eligible tables applied once per Workbench Input and Workbench Output for each fetch, by `WORKBENCH_COPY_PATCHES`, through `onUpdateNode`, with an `isCurrent` that checks the generation, while the document is editable and its top level shows; `nodeCreated` for a node a palette drop created. |
| `frontend/src/panels/editors/WorkbenchInputEditor.tsx` | The Workbench Input's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Input: the tables read-only with each label's `apiInputLabelIssue`, an "Edit in <label>" button calling `showView`, a note on what previews run on, chosen by whether the config's sample is a non-empty object, a note when no installed extension supplies tables, and one while a submodel is open (`insideSubmodel`, passed down from `frontend/src/panels/NodePanel.tsx`). It exports `WorkbenchTable`, one table read-only, and `WorkbenchTablesHeader`, the section's title, its "Edit in <label>" button and its notes, which the Workbench Output's panel shares. |
| `frontend/src/panels/editors/WorkbenchOutputEditor.tsx` | The Workbench Output's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Output: under `WorkbenchTablesHeader`, each table with its label's `apiInputLabelIssue`, the node connected to its port (from the `targetHandle` of the panel's input sources) or "Not connected", and a row per column with a select of the connected frame's columns (the input source's `columns`) that fills it, chosen by `mappedSource`, through `onUpdate` with the new `mapping`. |
| `frontend/src/extensions/ViewSwitcher.tsx` | The view switcher: "Pricing" (the pipeline editor) and one button per extension, labelled with the extension's `label`, the active one `aria-pressed`, on the shared `.toolbar-btn` surface. Full width as a column of labelled buttons, one per row and each as wide as the palette, or `compact` as a column of icon buttons for the collapsed palette strip. Renders nothing until the list holds an extension. |
| `frontend/src/extensions/ExtensionView.tsx` | Hosts one extension's view over the area below the toolbar: loads its module, calls `mount` with the view element, the toolbar slot, its API base, `SWITCHER_SLOT`, the palette's state (`useUIStore`'s `paletteOpen` when it mounts, and `setPaletteOpen`) and a `priceSample` that calls the editor shell's `priceSample` prop as it is when called, fills that slot with the switcher once mounted (compact while the palette is collapsed), registers the module's `save` (or none) as the store's `viewSave` while mounted, unmounts on leave, and shows loading or the failure beside a fallback switcher column until the view mounts. Beside the view it shows the Git panel while `gitOpen` is set, or else the Assistant panel while `assistantOpen` is, taking `onSave` for the Git panel and `isInsideSubmodel` and `readOnly` for the Assistant panel from the editor shell. |
| `frontend/src/extensions/priceSample.ts` | `priceSample(graph, workbench, source)`, a sample priced on *graph*: it gives each top-level node `WORKBENCH_COPY_PATCHES` has a rule for the update its copy needs to match *workbench*, in a copy of the graph, previews the first top-level Workbench Output once per label of `workbenchOutputTableLabels` with that `portLabel`, in parallel, and resolves `{tables}` with each table's `preview` rows by label; a preview whose status is not `ok` throws its `error`, and a failed preview request the request's `apiErrorMessage`. |
| `frontend/src/extensions/loadExtensionModule.ts` | The browser contract: `SWITCHER_SLOT`, `ExtensionMountOptions` (with `ExtensionPalette`, `WorkbenchTables` and `PricedSample`), `ExtensionHandle`, `ExtensionModule`, `loadExtensionModule` (dynamic import from the listed URL, kept out of Vite's graph, checked for a `mount` function and, when it exports one, a `save` function) and `mountExtension` (calls `mount` and checks it returned a handle with `unmount`). |

`src/haute/schemas.py` defines `ExtensionInfo`, `ExtensionsResponse` and
`QuoteTablesResponse`; `scripts/generate_api_contracts.py` lists the two responses as the
`extensions` response group; `frontend/src/api/types.ts` re-exports the generated types.

The Workbench Input reaches across Haute. `src/haute/_types.py` declares
`NodeType.WORKBENCH_INPUT`, its `workbench_input` decorator name, `WorkbenchInputConfig` and
`REQUEST_INPUT_NODE_TYPES`; `workbench_input` is a decorator of `NodeRegistry` in
`src/haute/pipeline.py`, so `Pipeline` and `Submodel` both have it; `src/haute/_config_io.py`,
`src/haute/_config_validation.py`, `src/haute/_cache.py` and `src/haute/node_defaults.json` give
it its folder, config shape, cache classification and default; `_build_api_input` in
`src/haute/_builders.py` and `_gen_api_input` in `src/haute/_codegen_builders.py` are
registered for both request inputs. Every check under `src/haute` that asks whether a node
reads the request tests membership of `REQUEST_INPUT_NODE_TYPES` (in execution, projection,
seeding, input preparation, sizing, data points, standalone nodes, parsing, identities,
recovery, saving, tracing, scoring and deploy), and every mapping keyed by node type that
has a Quote Input entry has the same Workbench Input entry, except the two in
`src/haute/execution.py` that name the file each node type reads,
`_SOURCE_PATH_CONFIG_BY_NODE_TYPE` and `_LOCAL_RUNTIME_INPUT_PATH_FIELDS_BY_NODE_TYPE`: a
Workbench Input reads none. Its frames without a request, read from its sample, come from
`workbench_table_frames` in `src/haute/_json_shred/_cache.py`, through
`resolve_workbench_input_from_config` in `src/haute/_node_apply.py`; its ports from
`workbench_table_labels`, which reads the tables alone; and its request's schema, for deploy
and for checking the sample's shape, from `request_record_schema` in
`src/haute/_json_shred/_shred.py`. `src/haute/_graph_utils.py`, which
`haute._types` imports, holds the same set as plain values, `REQUEST_INPUT_KINDS`.
`src/haute/_graph_shape.py` holds `SINGLETON_NODE_GROUPS` and `validate_singleton_groups`,
which save (`src/haute/routes/_save_pipeline.py`) and deploy (`resolve_config` in
`src/haute/deploy/_config.py`) enforce and the assistant's capability manifest
(`src/haute/assistant/_catalog.py`) reads; `src/haute/assistant/_ops.py` refuses to author a
Workbench Input, and its node card is `src/haute/assistant/assets/node_cards/workbenchInput.json`.
The input cache's request keeps `node_type: "apiInput"` for both request inputs: there it says
how a source is read. In the editor, `frontend/src/utils/nodeTypes.ts` holds the Workbench
Input's `NODE_TYPE_META` entry, `REQUEST_INPUT_TYPES`, `isRequestInputType`,
`singletonTypesOccupiedBy` and `singletonLimitMessage`, and every request-input check uses
`isRequestInputType`; `frontend/src/panels/NodePalette.tsx` shows the Workbench Input in the
Quote Input's place while a supplier is listed and drags it with `paletteWorkbenchInputConfig`;
`applyApiInputConfigChange` in `frontend/src/utils/apiInputPorts.ts` takes `followNames`, which
`frontend/src/utils/nodeUpdatePlan.ts` sets for a Workbench Input; `onUpdateNode` in
`frontend/src/hooks/useGraphCommitController.ts` takes `NodeUpdateOptions`; `onDrop` in
`frontend/src/hooks/useEdgeHandlers.ts` reports the node it creates to `onNodeCreated`; and
`NodeConfigEditor` renders `WorkbenchInputEditor` for a Workbench Input and `ApiInputEditor`, the
table editor, for a Quote Input.

The Workbench Output reaches across Haute the same way. `src/haute/_types.py` declares
`NodeType.WORKBENCH_OUTPUT`, its `workbench_output` decorator name, `WorkbenchOutputConfig`,
its place in `SINK_ONLY_NODE_TYPES`, and `RESPONSE_NODE_TYPES`, the Quote Response and the
Workbench Output; `workbench_output` is a decorator of `NodeRegistry`; `src/haute/_config_io.py`,
`src/haute/_config_validation.py`, `src/haute/_config_builder.py`, `src/haute/_cache.py` and
`src/haute/node_defaults.json` give it its folder, config shape, config-folder parsing, cache
classification and default. `_build_workbench_output` in `src/haute/_builders.py` and the
standalone runner in `src/haute/_standalone_nodes.py` both call
`assemble_workbench_output_from_config` in `src/haute/_node_apply.py`, the first with the
target handles of the node's incoming edges and the second with the target ports of its
`pipeline.connect` calls, which `Pipeline` passes to the node it runs; `_gen_output` in
`src/haute/_codegen_builders.py` is registered for it. The checks that mean "the pipeline's
response" test membership of `RESPONSE_NODE_TYPES`: `Pipeline._resolve_output_node`,
`find_output_node` in `src/haute/deploy/_pruner.py`, the trace pass-through in
`src/haute/_trace_correlation.py`, and `SINGLETON_NODE_GROUPS`; `as_response` in
`src/haute/_workbench_output.py` turns either's result into the frame a request is answered
with, and `src/haute/projection.py` covers it with the generic contract rule. The assistant's
`src/haute/assistant/_ops.py` refuses to author a Workbench Output as it refuses a Workbench
Input, and its node card is `src/haute/assistant/assets/node_cards/workbenchOutput.json`. In
the editor, `frontend/src/utils/nodeTypes.ts` holds its `NODE_TYPE_META` entry,
`RESPONSE_TYPES` and `isResponseType`, which `singletonTypesOccupiedBy` and
`singletonLimitMessage` use for the response pair; `frontend/src/nodes/PipelineNode.tsx` renders
its tables as target port rows through `FramePortRows`; `frontend/src/panels/NodePalette.tsx`
shows it in the Quote Response's place while `responseTablesSupplier` finds a supplier and drags
it with `paletteWorkbenchOutputConfig`; `validatePipelineConnection` in
`frontend/src/utils/connectionValidation.ts` takes a connection to it only on a table's port;
`prepareNodeUpdate` in `frontend/src/utils/nodeUpdatePlan.ts` removes the connections whose port
its new tables lack, which `useGraphCommitController` reports; and `InputSource` in
`frontend/src/panels/editors/_shared.tsx` carries each input's `targetHandle` for its panel.

## Key types and data structures

- **Entry point object.** Whatever `EntryPoint.load()` returns (Obverse uses a module) must
  have `label: str` (non-empty), `assets_dir: pathlib.Path`, `entry: str` (a plain file name:
  no `/`, `\`, or `..`) and a callable `create_router(project_dir: Path) -> fastapi.APIRouter`.
  Haute checks these with `getattr`, so the extension needs no import from Haute. It may also
  have `quote_tables(project_dir: Path) -> list`, the quote's tables in the Quote Input's v2
  shape as its files define them now; `None` or absent supplies none. Beside it, it may
  have `quote_sample(project_dir: Path) -> dict | None`, one quote as a request holds it, as
  its files define it now, and `response_tables(project_dir: Path) -> list`, the response's
  tables in the same shape, as its files define them now.
- **`Extension`** (`src/haute/_extensions.py`): `name`, `label`, `router`, `assets_dir`,
  `entry`, and `quote_tables`, `quote_sample` and `response_tables`, the extension's functions
  bound to the project directory, or `None`. `api_base` is `/api/extensions/<name>`, `assets_url` is `/extensions/<name>` and
  `entry_url` is `/extensions/<name>/<entry>`.
- **`ExtensionInfo`** (`src/haute/schemas.py`): `name`, `label`, `api_base`, `entry_url`,
  `ready` (the entry file exists now) and `detail` (`null` when ready, otherwise what to do).
  **`ExtensionsResponse`**: `extensions: list[ExtensionInfo]`, in name order. `ExtensionInfo`
  also has `quote_tables`, true for the extension that supplies the quote's tables, and
  `response_tables`, true when that extension supplies the response's tables too.
  **`QuoteTablesResponse`**: `extension` (its name), `tables` (the v2 tables), `sample`
  (an object, `{}` for none) and `response_tables` (the v2 tables, `[]` for none).
- **Store state** (`frontend/src/stores/useExtensionsStore.ts`): `extensions` (empty until
  a non-empty list loads), `activeView` (`PIPELINE_VIEW` initially), `toolbarSlot`
  (`HTMLElement | null`), `viewSave` (`(() => Promise<boolean>) | null`), and `quoteTables`
  (`QuoteTables | null`: `extension`, `tables`, `sample`, `responseTables` and `fetch`, the
  fetch's number).
- **`NodeUpdateOptions`** (`frontend/src/hooks/useGraphCommitController.ts`): `isCurrent`, which
  returning false makes an update count as superseded, and `onSettled`, called once with the
  update's final result: at once for an ordinary node, after identity resolution for a request
  input, whose call returns before it commits.
- **`REQUEST_INPUT_NODE_TYPES`** (`src/haute/_types.py`): the frozenset of `NodeType.API_INPUT`
  and `NodeType.WORKBENCH_INPUT`; `REQUEST_INPUT_KINDS` (`src/haute/_graph_utils.py`) holds their
  values and `REQUEST_INPUT_TYPES` (`frontend/src/utils/nodeTypes.ts`) is the editor's twin.
  **`WorkbenchInputConfig`**: `tables` and `sample`.
- **`SINGLETON_NODE_GROUPS`** (`src/haute/_graph_shape.py`): `(REQUEST_INPUT_NODE_TYPES, "Quote
  Input or Workbench Input")` and `(RESPONSE_NODE_TYPES, "Quote Response or Workbench Output")`.
- **`RESPONSE_NODE_TYPES`** (`src/haute/_types.py`): the frozenset of `NodeType.OUTPUT` and
  `NodeType.WORKBENCH_OUTPUT`; `RESPONSE_TYPES` (`frontend/src/utils/nodeTypes.ts`) is the
  editor's twin. **`WorkbenchOutputConfig`**: `tables`, the response's tables in the v2 shape,
  and `mapping`, `dict[str, dict[str, str | None]]` by table label and column name: a frame
  column's name, or `None` for none; a column without an entry is filled by name.
- **A Workbench Output's tables** (`src/haute/_workbench_output.py`): each table is one-row
  when its path is the root, `$[:]`, and many-row when its path has one array step below it,
  `$[:].<name>[:]`; every column's path must have its table's path as its array prefix (no
  `[:]` after it), so it sits at its table's level. A port is a table's label. A column's
  declared type admits a frame's column of that dtype or `pl.Null`, and also any integer
  dtype for `int`, any integer, float or decimal dtype for `float`, and `pl.Categorical` or
  `pl.Enum` for `str`; the column is cast to the declared dtype (`_POLARS_TYPE_MAP`).
- **`assemble_workbench_output_from_config(*dfs, config, base_dir=None, ports=None)`**
  (`src/haute/_node_apply.py`): `dfs` are the incoming frames in edge order and `ports` the
  target port of each, aligned; without `ports` it refuses to run. It returns
  `WorkbenchOutputTables`, a `dict` of lazy frames by table label with the tables as `.tables`.
- **Browser contract** (`frontend/src/extensions/loadExtensionModule.ts`): the module exports
  `mount(options: ExtensionMountOptions): ExtensionHandle`, where the options are `main` (the
  element filling the area below the toolbar), `toolbar` (the toolbar's slot element),
  `apiBase`, `switcherSlot` (`SWITCHER_SLOT`, `"haute-view-switcher"`), `palette`
  (`ExtensionPalette`: `open`, whether the node palette was open when the view mounted, and
  `setOpen(open)`, which opens or collapses it) and
  `priceSample(workbench: WorkbenchTables): Promise<PricedSample>`, `WorkbenchTables` being
  `{tables, sample, response_tables}` as `GET /api/quote-tables` serves them and
  `PricedSample` being `{tables}`, each Workbench Output table's rows (objects by column
  name) under its label, and the handle has `unmount()`. The extension
  shows its palette open or collapsed to match and calls `setOpen` from its own minimiser, so
  both views share one palette state. The module may also export `save(): Promise<boolean>`,
  which writes the view's unsaved work to the project and resolves whether it saved; the
  toolbar's Save calls it while the view shows. The extension may attach a shadow root to `main` and
  `toolbar`; the light-DOM child Haute places in `main` carries `slot="haute-view-switcher"`.
- **The toolbar kit** (`frontend/src/haute-ui/`, specified in
  [frontend-shared](../frontend-shared/low-level.md)): an extension installs the directory
  as the `haute-ui` package (Obverse uses an npm `file:` dependency on the Haute checkout
  beside it), renders its toolbar controls and palette shell with the kit's components, and
  imports `haute-ui/tokens.css`, `haute-ui/toolbar.css` and `haute-ui/palette.css` into the
  stylesheet its shadow roots adopt. The kit's tokens are on `:root, :host`, so they apply inside a shadow root. The
  slot sits after Haute's brand column, so the extension's first control starts over the
  palette's edge, as Haute's first pipeline control does.

## Control flow

1. **Import.** After `haute.server` includes its feature routers it calls
   `mount_extensions(app, discover_extensions(Path.cwd()))`, then registers its `/api` and
   `/ws` 404 guards and, when a build is present, the SPA catch-all.
2. **Discovery.** `discover_extensions` reads `importlib.metadata.entry_points(group=...)`,
   rejects duplicate names, then for each entry point in name order validates the name,
   loads the object, checks its attributes, and calls `create_router(project_dir)` once.
3. **Mounting.** For each extension, `mount_extensions` includes the router with prefix
   `api_base` and a `GET <assets_url>/{path}` route that serves a file inside `assets_dir`
   with `Cache-Control: no-cache` (so a rebuilt front end shows on the next page load) and
   answers 404 for anything else. It is a route rather than a `StaticFiles` mount, which
   answers 500 until its directory exists. Then it includes the `GET /api/extensions` route,
   which builds an `ExtensionInfo` per extension, checking the entry file on every request.
4. **Dev serving.** `haute.cli._serve._run_dev_mode` passes the Haute package directory and
   `extension_package_dirs()` as uvicorn's `reload_dirs`. `extension_package_dirs` resolves
   each entry point's top-level module with `importlib.util.find_spec`, so it imports
   nothing; a top-level module that is not a package adds no directory. Vite's dev server
   proxies `/extensions` to the backend.
5. **Editor start.** The editor shell calls `useExtensionsStore.load()` once. A non-empty
   list is stored; an empty one changes nothing; a failure shows an error toast naming the
   cause and the list stays empty.
6. **Switching to an extension.** A switcher button calls `showView(name)`. The shell then
   hides the pipeline region (`invisible` plus `inert`), passes `enabled: false` to
   `useKeyboardShortcuts`, skips its Ctrl/Cmd+Enter handler, gives React Flow `null`
   delete and pan-activation keys, and renders `ExtensionView` (lazy) over that region. A
   subscription to the store's view changes closes the context and connection-drop menus
   when the pipeline view is left. The toolbar renders its brand, a slot element it
   registers with `setToolbarSlot`, and the project controls (Assistant, Help, the branch
   indicator with Save and Commit). The shell passes `gitOpen` and `assistantOpen` to the
   node properties panel only while the pipeline view is active, so `ExtensionView` shows
   those panels instead and neither mounts twice.
7. **Mounting the view.** Once the toolbar slot exists, `ExtensionView` checks `ready`
   (not ready: show `detail`), imports the module through `loadExtensionModule(entry_url)`,
   and calls `mountExtension` with its own host element and the palette's state, read from
   `useUIStore` when it mounts. The palette state is not an effect dependency, so opening or
   collapsing the palette never remounts the view. It sets the store's `viewSave` to the
   module's `save`, or none, and clears it on leave. The editor's Save (toolbar or Ctrl+S in
   the pipeline view) first asks `saveView()`, so in a view with a `save` it saves that
   view's work instead of the pipeline. Then it renders
   `<div slot="haute-view-switcher">` holding a `ViewSwitcher` inside the host, compact while
   `paletteOpen` is false.
8. **Switching back.** `showView(PIPELINE_VIEW)` unmounts `ExtensionView`, whose effect
   cleanup calls the handle's `unmount()`; the toolbar slot unregisters, and the pipeline
   region becomes visible and interactive with its shortcuts on.
9. **The Quote Input's tables, served.** `GET /api/quote-tables` finds the extension whose
   `quote_tables` is set (404 with none), calls it in the thread pool, answers 500 naming the
   extension when it raises (logged with its traceback) or returns something other than a
   list, checks the list with `validate_v2_schema({"tables": tables})`, answering the
   structured 422 from `api_input_schema_error_response` when that fails. It then calls
   `quote_sample`, when the extension has one, in the thread pool, answering 500 naming the
   extension when it raises or returns something other than a `dict` or `None`, and answers
   `QuoteTablesResponse` with the tables and the sample (`{}` for none), the sample unchecked.
10. **Fetched.** `useExtensionQuoteTables` calls `refreshQuoteTables()` when a supplier is
    listed and whenever `executionGeneration` advances, which it does on every document
    adoption and never on a save's acknowledgement, and records the fetch's number against
    the generation; `showView` calls it when the pipeline view shows after an extension's.
    Each fetch has a number, and only the newest settles: into `quoteTables`, or into one
    error toast naming the extension's label that leaves `quoteTables` as it was.
11. **Applied.** The tables are eligible when `quoteTables.fetch` is at least the number
    recorded for the current generation. While they are, `editingReadOnly` is false and the
    view stack has one entry, the hook calls `onUpdateNode` for each Workbench Input on the
    canvas that has not had this fetch, with `quoteTablesPatch` applied (none when the copy
    matches) and an `isCurrent` that checks the generation. The commit controller checks
    `isCurrent` before committing, again after identity resolution, and `prepareNodeUpdate`
    runs `applyApiInputConfigChange` with `followNames`, which keeps only the connections whose
    labels remain and reports the rest. The hook reads the nodes
    through `App`'s graph ref, so graph edits, undo and redo never run it. When a palette
    drop creates a node, `onDrop` reports it to `onNodeCreated`, the hook's `nodeCreated`,
    which applies the eligible tables to that node once it is on the canvas.
12. **Shown.** While `quoteTablesSupplier` finds a listed supplier, `NodePalette` renders the
    Workbench Input in the Quote Input's place in `PALETTE_TYPES` and drags it with
    `paletteWorkbenchInputConfig`: the supplier's latest tables and sample, or `[]` and `{}`
    before its first fetch. `NodeConfigEditor` renders `WorkbenchInputEditor` for it.
13. **One request input.** Save's `_validate_singletons` and `resolve_config`, before
    `prune_for_deploy`, call `validate_singleton_groups` on the flattened pipeline. In the
    editor, `singletonTypesInDocument`, the drop check and the paste check count occupied
    singleton types through `singletonTypesOccupiedBy`, which gives both request inputs for
    either, and a refused drop toasts `singletonLimitMessage`.
14. **Frames without a request.** `_build_api_input` gives a Workbench Input a source that
    calls `resolve_workbench_input_from_config` with the ports' demanded columns, and the
    standalone runner in `src/haute/_standalone_nodes.py` calls it too. The resolver loads the
    config as `resolve_api_input_from_config` does. `workbench_table_frames` parses the
    emitting tables as `load_v2_api_source` does (`_emitting_table_specs`, then
    `_projected_table_specs` for the demand). With no sample (absent, `None` or `{}`) each
    port is a one-row `LazyFrame` with `_declared_frame_schema`'s dtypes and null values.
    With one, it reads the sample whole before it projects: the sample must be a `dict`;
    `_check_sample_shape` walks it against `request_record_schema(config)` (a value under a
    `pl.Struct` a `dict` or `None`, one under a `pl.List` a `list` or `None`, each element of a
    list of `pl.Struct` a `dict` and each element of a list of values neither a `dict` nor a
    `list`; keys the schema does not name are not walked); `shred_to_buffers([sample], ...)`
    with the complete specs, a `ShredSkipStats` and a row sink gathers each table's rows, any
    skip being a misfit; and `_rows_to_frame` types every table's rows. Each port is then its
    complete frame cut to the projected columns, or the one-row null frame when it has no
    rows. A misfit raises `ApiInputSchemaError` "The workbench's sample does not fit this
    Workbench Input's tables: <reason>. Correct it in the workbench and save it.", a public
    contract error, so a preview in its worker answers 422 with it. `snapshot_backed_inputs`
    leaves the node out, since it has no structured path; `own_facts` in
    `src/haute/_seed_plans.py` reports it cheap and slice-transparent, and the bounded-admission
    check admits it without a read; `api_input_port_metadata` in `src/haute/_ram_estimate.py`
    reads `workbench_table_labels` first, answering no metadata when the tables fail or lack
    the port, then sizes the port from its frame through `_detailed_dataframe_metadata`,
    letting a sample's misfit through.
15. **Its table points.** In `src/haute/_data_points.py` a Workbench Input's table point keeps
    the `api_input_table` kind but `_resolve_workbench_table` resolves it current at once from
    `workbench_table_labels`, never the sample, versioned by its tables, its sample and its
    lineage, with no input identity, so `src/haute/routes/_node_data_service.py` reports it as
    reading directly. Leasing it runs `workbench_table_frames` for the port and projects the
    demand; running it answers "A Workbench
    Input's tables are read directly; there is nothing to cache."; `clearDelegatedData` in
    `frontend/src/hooks/useNodeDataCache.ts` clears nothing for a point that reads directly.
    For the cache report, `api_input_table_labels` takes the labels from
    `workbench_table_labels` and `api_input_table_digests` gives none, so `points_for_graph` reports the tables
    together as one row that reads directly.
16. **Deploy.** Deploy reads a Workbench Input's tables, never its sample. For a Workbench
    Input, `infer_input_schema` in `src/haute/deploy/_schema.py`
    returns `request_record_schema` of its tables as dtype strings, through
    `_workbench_request_schema`, and `_read_sample_row`, which execution-policy planning and
    the output-schema dry run share, returns one record of nulls in that schema.
    `request_record_schema` walks each emitting table's path, then each selected column's,
    hop by hop as `parse_table_path` and `_parse_dollar_path` split them: an object hop is a
    `pl.Struct` field, an array hop a `pl.List` of `pl.Struct`, and the leaf has its declared
    type; a column at the reserved `$value` leaf makes its array a `pl.List` of that type, and
    a table whose selected columns all sit at an ancestor level keeps its own array, as a
    `pl.List` of an empty `pl.Struct`. When the dry run falls back to the hard-capped batch
    worker, `_capped_worker_output_schema` passes the sample's schema to
    `prepare_batch_scoring` as `input_schema`, carried on `BatchScoreRequest`, and the worker
    builds its input frame with it, so a record of nulls keeps its types; a served request
    passes none.
17. **The response's tables, served and applied.** `_load` binds `response_tables` as it binds
    `quote_sample`. `GET /api/quote-tables` calls it after the tables, in the thread pool,
    answering 500 naming the extension when it raises or returns something other than a list,
    checks the list with `validate_v2_schema({"tables": response_tables})`, answering the
    structured 422 when that fails, and answers them as `response_tables`, `[]` without the
    function. `refreshQuoteTables` keeps them in `quoteTables.responseTables`, and
    `useExtensionQuoteTables` applies them in the same pass and under the same rules as the
    tables: for each Workbench Output on the canvas that has not had this fetch, it calls
    `onUpdateNode` with `responseTablesPatch` (none when the copy matches). There
    `prepareNodeUpdate` keeps each connection into the node whose `targetHandle` is one of the
    new tables' labels and removes the rest, which the commit controller reports in one
    warning toast as connections whose tables no longer exist.
18. **Shown and connected.** While `responseTablesSupplier` finds a listed supplier whose
    `response_tables` is true, `NodePalette` renders the Workbench Output in the Quote
    Response's place and drags it with `paletteWorkbenchOutputConfig`: the latest response
    tables, or `[]` before the first fetch. `PipelineNode` renders a Workbench Output's body as
    `FramePortRows` with `direction="target"`, one row per label from
    `workbenchOutputTableLabels`, each a target `Handle` whose id is the label, or "No tables"
    with none; it has no default input and no source handle, and the labels join the signature
    that re-measures the node's handles. `validatePipelineConnection` refuses a connection into
    a Workbench Output whose target handle is none of its tables ("Connect to one of
    <node>'s tables"), one to a table that already has a connection ("<table> is already
    filled by <node>"), and one from a node that already fills another of its tables
    ("<source> already fills <table>; a node fills one table"), before the shared input-name
    check. `onConnect` keeps the handle as the edge's `targetHandle`, and save and codegen
    write it as `target_port`.
19. **Run.** `_build_workbench_output` builds a function of the incoming frames that calls
    `assemble_workbench_output_from_config` with the config and `ports=ctx.target_handles`. It
    is registered opaque, so projection asks its parents for their whole frames: the mapping may
    pick any of their columns, and a mapping entry naming a column a frame lacks reaches the
    node, to be named, rather than failing a projection contract upstream. With nothing
    connected it reports itself a source, so the walk calls it with no frames and it says what
    to connect. The standalone runner calls it with the node's sidecar and `target_ports`:
    `Pipeline._execute_transform` passes the target port of each incoming `pipeline.connect`,
    through `Node._invoke` and `run_configured_node`. The function pairs each frame with its
    port, raising when a port is missing, is none of the tables' labels or repeats one, reads
    `workbench_output_tables` and `workbench_output_mapping`, and calls
    `fill_workbench_tables`, which for each table takes its frame (raising when none is
    connected) and, against its `collect_schema()`, gives each column its source: the mapping's
    entry, else the column of the same name when the frame has it, else none. A source the frame
    lacks or whose dtype does not fit raises; a column with no source is a typed null literal.
    The table is the frame with each column added under its own name, cast to its declared
    dtype, then cut to the table's columns in order, so a pick may swap two names. A one-row
    table is wrapped in `limited_python_scan` of a producer that collects it through
    `execution_collect` and raises unless it has exactly one row. Nothing is collected while
    the tables are built, so a schema-only walk reads them as it reads any frame.
20. **Its tables, previewed.** The walk keeps the result as a multi-frame bundle, as it keeps
    a Workbench Input's, so the preview of a Workbench Output with several tables shows the
    table named by the request's `port_label`, its `frame_columns` listing every table for the
    preview's table picker, and one with a single table shows that table.
    `previewPortLabel` in `frontend/src/hooks/usePipelineAPI.ts` asks for the first of
    `workbenchOutputTableLabels`. `is_node_output` in `src/haute/_seed_plans.py` never makes a
    Workbench Output a node-output snapshot point, as it never makes a request input one: a
    bundle is not one frame, so a preview with shared snapshots never captures it, though two
    inputs make it a join point. The executor renders only a Quote Response's preview with
    `render_output_document`. `execute_trace` in `src/haute/trace.py` refuses a Workbench
    Output's bundle as it refuses any multi-frame target, saying to trace the node connected to
    the table instead.
21. **The response node.** `Pipeline._resolve_output_node` returns the one node whose
    `_node_type` is in `RESPONSE_NODE_TYPES`, raising `ExecutionError` naming them when there
    are several; `find_output_node` picks the response node the same way; trace correlation
    carries values through a response node unchanged. Where the pipeline answers a request,
    `as_response` turns the response node's result into the frame it answers with:
    `Pipeline.run` and `Pipeline.score` before collecting, and `_score_graph_lazy` in
    `src/haute/deploy/_scorer.py`, which a deployed pipeline's `/quote`, test quotes, the
    output-schema dry run and batch scoring share, before selecting `output_fields`. For a
    Workbench Output it calls `workbench_response`: the tables' columns renamed to their paths,
    the response's schema from `output_document_schema` with an identity mapping (the one-row
    tables' columns as one source frame and each many-row table's as its own), and a
    `limited_python_scan` whose producer collects the one-row tables, puts their single rows
    side by side with `pl.concat(how="horizontal")`, and builds the document with
    `assemble_output_from_mapping`. A Quote Response's result is its document already, so
    `_quote_response_content` renders either unchanged.
22. **One response node.** `validate_singleton_groups` counts both response node types as one
    group. In the editor, `singletonTypesOccupiedBy` gives both for either, so the palette greys
    out the one it offers and the drop and paste checks refuse a second, with
    `singletonLimitMessage` naming "Quote Response or Workbench Output".
23. **The sample, priced.** The editor shell passes `ExtensionView` a `priceSample` that calls
    `priceSample(resolveGraphFromRefs(...), workbench, activeSource)`: the document's top level
    even while a submodel is open, and the settings store's active source, both read when it is
    called. `ExtensionView` gives the extension a function that calls the prop it holds then,
    so the prop is not an effect dependency and never remounts the view. `priceSample` replaces
    the config of each top-level node whose type `WORKBENCH_COPY_PATCHES` names with the config
    patched as that rule says for `workbench`, when it says to, in a copy of the graph; a
    submodel's nodes keep theirs, as the copy hook leaves them. It previews the first top-level
    `workbenchOutput` with `row_limit` 10,000, the preview route's maximum, once per label in
    parallel, each with its `port_label` and so a supersession key of its own, and gathers
    each `preview` under its label. Nothing it does reaches the graph store.

## Edge cases and invariants

- Extension routes are registered before the 404 guards and the SPA catch-all, so `GET`
  routes under `/api/extensions/<name>` and files under `/extensions/<name>/` are reached.
- Extension APIs are under `/api/`, so the session-cookie check applies; assets are not.
- The pipeline editor is never unmounted by a view switch; only its visibility,
  interactivity and keyboard handling change.
- React never reuses an element an extension may have given a shadow root (the toolbar
  slot, the view host) for other content: the toolbar's two forms are keyed by view and
  `ExtensionView` by extension name. A reused element would keep the shadow root and hide
  whatever React rendered into it.
- While an extension's view shows, no Haute window-level key handler acts: the canvas
  shortcuts, Ctrl/Cmd+Enter and React Flow's delete and pan keys are all off. The editor's
  own `isTyping` checks cannot see into an extension's shadow root, which is why the
  handlers are switched off rather than trusted to ignore extension keys.
- The switcher is rendered outside the read-only `inert` node palette, so it stays usable
  while the pipeline is read-only.
- `ExtensionView` calls `mount` at most once per mounted host, always calls `unmount` on
  leave, and never calls `mount` once it has left, even when the module arrives later.
- An extension's module is imported from its listed URL with `/* @vite-ignore */`, so it is
  never part of Haute's bundle or its size budget.
- With no extensions installed nothing renders: no switcher, no toolbar slot, no proxy
  traffic beyond the one listing request.
- Only a Workbench Input takes an extension's tables: a fetch never changes a Quote Input,
  whose connections still follow a rename by position as `migrateApiInputEdges` allows.
- Each Workbench Input gets each fetch's tables at most once per generation: the fetch-driven
  pass and a palette drop's report never send the same update twice, and an undone update
  stays undone until the next fetch. An update that does not commit, such as one whose node a
  submodel hid while its identity was resolving, is forgotten through `onSettled`, so the next
  pass, when the top level shows again or the next fetch arrives, sends it again.
- A fetch that completes while a submodel is open, or the document is read-only, applies
  when the top level shows again and the document can change.
- An empty `tables` list is a valid answer: the Workbench Input has no ports, and
  `workbench_table_labels` and `workbench_table_frames` refuse to make any.
- A Workbench Input's sample is read whole whatever a reader asks of it, so every reader finds
  the same misfit; resolving its points reads the tables alone and never does. A missing or
  `None` part reads as in a request: under a `pl.List` the table has no rows, and under a
  `pl.Struct` the fields under it are null while the row and its other values stay.
- A Workbench Input's table point is never built or cleared; the input cache, its routes and
  the Quote Input's snapshots are as they are without it.
- A Workbench Input inside a submodel definition is never updated by a fetch; its panel says
  so while that submodel is open.
- Tables and samples are compared as JSON with object keys sorted, so a copy written to its
  config file and read back never reads as changed.
- A Workbench Output's connections are bound by `targetHandle`, never by position or input
  name: reordering its tables moves no connection, and an upstream rename leaves its bindings
  as they are.
- A fetch never changes a Quote Response, and a Workbench Output inside a submodel definition
  keeps its copy.
- An empty `response_tables` list is a valid answer: the Workbench Output has no ports, and
  running it fails.
- The one-row tables' rows are put side by side only once each has been shown to hold exactly
  one row, so no row is paired by position with another quote's.
- `priceSample` never changes the document: the copies it gives the workbench nodes are in its
  request's graph alone, and the pipeline view gives the document the ones the extension saved
  when it next shows.

## Error handling

- `ExtensionError` (a `RuntimeError`) carries the entry point's name and value and the
  reason; an import or `create_router` failure is chained with `from`. It propagates out of
  `import haute.server`.
- The listing route raises nothing for a missing build: `ready` is false and `detail` names
  the missing file and says to build the extension's front end.
- `fetchExtensions` throws `ApiError` or a contract error like any typed request; the store
  turns it into one error toast.
- `showView` throws an `Error` for a view that is not installed: only the switcher calls
  it, with names from the list.
- `loadExtensionModule` throws when the module has no `mount` function; `mountExtension`
  throws when `mount` throws or returns no `unmount`. `ExtensionView` shows either message.
- `_load` raises `ExtensionError` for a `quote_tables`, `quote_sample` or `response_tables`
  that is not callable and for a `quote_sample` or `response_tables` without `quote_tables`,
  and `discover_extensions` for more than one supplier, naming every one.
- `GET /api/quote-tables` raises `HTTPException` 404 or 500, chaining the extension's error,
  and answers schema errors through `api_input_schema_error_response`; the request client
  retries a 5xx like any idempotent request before the store sees the failure.
- An update superseded through `isCurrent` resolves `{ok: false}` with the commit
  controller's message, which it toasts, and changes nothing.
- `validate_singleton_groups` raises `ValueError` naming the group and how many it found;
  save answers it as a 400 and `resolve_config` lets it propagate. The assistant's
  operations raise `OpValidationError` for a Workbench Input they may not author.
- `workbench_table_labels` and `workbench_table_frames` raise `RuntimeError` "This Workbench
  Input has no tables: add input tables to the workbench's schema and save it." when no table
  emits, and `workbench_table_frames` raises `ApiInputSchemaError` for a sample that does not
  fit, naming what does not;
  `api_input_table_labels` turns it into `NodeDataPointInvalidError`, an error row in the
  cache report. `request_record_schema` raises `ApiInputSchemaError` when two paths disagree
  about a request field, which `_workbench_request_schema` turns into a `ValueError` naming
  the node, as it does for tables that give no schema.
- `WorkbenchOutputError`, an `ExecutionError` with no public error code, carries every
  failure of a Workbench Output's run: no tables, a port with no connection, a connection on a
  port that is none of its tables or on a port another connection has, a mapping entry for a
  table or column it does not have, a mapping entry naming a column its frame lacks, a column
  whose dtype does not fit, and a one-row table without exactly one row. It names the table. A preview shows it on the node, `run()` and `score()` raise it, and a
  deployed pipeline answers it as its other internal errors, a 500 carrying the message.
  Tables that break the v2 rules raise `ApiInputSchemaError` from `validate_v2_schema`, and a
  table whose path or a column's path is not of the shapes above raises `WorkbenchOutputError`.
- The assistant's operations raise `OpValidationError` for a Workbench Output they may not
  author, as for a Workbench Input.
- `priceSample` throws an `Error` "The pipeline has no Workbench Output: add one from the
  palette and connect a frame to each of its tables." when the graph's top level has none,
  and an `Error` carrying a table's preview `error` when its status is not `ok`; a failed
  `previewNode` request throws an `Error` carrying its `apiErrorMessage`, so the server's
  reason, such as a sample that does not fit, reaches the extension rather than a status.

## Testing

- `tests/test_extensions.py` covers discovery from real `EntryPoint` objects pointing at
  test packages (name order, `create_router` called with the project directory), mounting
  (an extension's `GET` route and asset reached in an app registered in `haute.server`'s
  order, a missing file and a path escaping the asset directory answered 404), the listing
  (`ready` and `detail` before and after the entry file exists, an empty list without
  extensions), every `ExtensionError` case, `extension_package_dirs` finding a package
  without importing it, and the real `haute.server` app answering `GET /api/extensions`
  ahead of its 404 guard and only with the session cookie.
- `tests/test_cli_serve.py` checks that dev mode passes extension package directories to
  uvicorn's `reload_dirs`.
- `frontend/src/stores/__tests__/useExtensionsStore.test.ts` covers loading through the
  generated contract, the untouched store for an empty list, the failure toast (also for a
  contract violation), `showView`, the toolbar slot, and `saveView` (left to the pipeline in
  the pipeline view or without a `save`, otherwise calling it and toasting the outcome).
- `frontend/src/extensions/__tests__/ViewSwitcher.test.tsx` covers the empty case, the
  buttons and their pressed state, switching, and the compact form.
- `frontend/src/extensions/__tests__/ExtensionView.test.tsx` covers mounting with the
  documented options, the slotted switcher, unmounting on leave, never mounting after a
  late load, waiting for the toolbar slot, an unbuilt extension, and load and mount
  failures keeping the switcher; the module's `save` held as `viewSave` while mounted; the palette state (the extension's `setOpen` collapses the
  palette, the switcher turns compact, and the view is not remounted); `priceSample` passing
  its tables to the shell's latest prop without remounting the view; and the Git or
  Assistant panel beside the view.
- `frontend/src/extensions/__tests__/priceSample.test.ts` covers the given copies taken by the
  request's top-level workbench nodes and not by the graph it was given or a submodel's, one
  preview per table with its `portLabel` and the rows gathered by label, a pipeline without a
  Workbench Output, a table whose preview fails, a preview request failing with the server's
  reason, and a Workbench Output with no tables.
- `frontend/src/extensions/__tests__/loadExtensionModule.test.ts` covers importing a module
  by URL and the module and handle checks, an optional `save` included.
- `frontend/src/hooks/__tests__/useKeyboardShortcuts.test.ts` checks that shortcuts do
  nothing while disabled and act again once enabled, and
  `frontend/src/components/__tests__/Toolbar.test.tsx` checks the toolbar in an
  extension's view and that switching back never reuses the shadow-rooted slot.
- `frontend/src/__tests__/App.integration.test.tsx` and
  `frontend/src/__tests__/App.backgroundJobsIsolation.test.tsx` stub the listing with no
  extensions.
- The quote's tables: `tests/test_extensions.py` covers the supplier listed and its
  tables served for the project directory, the 404 without one, and each failure (two
  suppliers, a non-callable `quote_tables`, a raising function, a non-list, a keyword label),
  and the sample served beside the tables (`{}` without one), a failing or misshapen sample
  answering 500 naming the extension, and a `quote_sample` without `quote_tables` or not a
  function refused;
  `tests/test_api_input_table_snapshots.py` shreds a two-quote request through tables in
  Obverse's shape into one frame per table; `tests/test_config_io.py` writes a Workbench
  Input's config file, its sample included, to `config/workbench_input/` and reads it back,
  and refuses one with a `path`; `tests/test_api_contracts.py` holds the extension routes in
  the API's fingerprint.
- The Workbench Input: `tests/test_request_inputs.py` runs the checker over `src/haute` after
  its fixtures show each allowed form passing and each other form reported; holds
  `REQUEST_INPUT_KINDS` and the editor's `REQUEST_INPUT_TYPES` equal to
  `REQUEST_INPUT_NODE_TYPES`; shows a Workbench Input beside a Constant reading a request as
  a Quote Input with the same tables does through codegen and parsing (with no `path` in its
  config file), data points, `Pipeline.score`, deploy scoring and trace lineage, while its
  preview yields nulls and it has no snapshot; previews one typed null row per table, with a
  join downstream admitted; reads its table points directly, in the cache report and through
  the node-data routes' point, run and clear too;
  derives its request schema and resolves a deploy without a sample file; runs its generated
  code to the same null rows and parses a submodel's `workbench_input`; previews, runs and
  leases its sample's rows, a downstream calculation included, with a null row for a table
  the sample leaves empty and a partly null object keeping its row
  (`test_a_workbench_input_previews_its_sample`); changes a table point's version with the
  sample; fails each kind of misfit wherever rows are read, a join's planning and the
  preview route's worker (422) included, while the cache report still has the points; never
  reads the sample for a request (`test_a_request_never_reads_the_sample`); and refuses a
  second request input in save and in `resolve_config`. `tests/test_deploy_batch_scoring.py` keeps a
  null sample's types in the hard-capped worker. `tests/test_assistant_ops.py` covers the
  assistant's refusals and its wiring to a Workbench Input's frames, and
  `tests/test_codegen_roundtrip_property.py` round-trips one in its corpus.
- In the frontend, `frontend/src/utils/__tests__/requestInputTypes.test.ts` runs the editor's
  guard over `frontend/src` after its fixtures, and covers `isRequestInputType` and the shared
  singleton slot; `frontend/src/utils/__tests__/extensionQuoteTables.test.ts` the copy rules;
  `frontend/src/utils/__tests__/apiInputPorts.test.ts` connections following names with
  `followNames`; `frontend/src/stores/__tests__/useExtensionsStore.test.ts` the numbered
  fetches and the sample each keeps; `frontend/src/hooks/__tests__/useExtensionQuoteTables.test.tsx`
  when tables and samples apply and to what, never a Quote Input;
  `frontend/src/hooks/__tests__/useGraphCommitController.pending.test.ts` an update superseded
  through `isCurrent`; `frontend/src/panels/__tests__/NodePalette.test.tsx` the palette's swap,
  drag, with the sample, and shared slot;
  `frontend/src/__tests__/editors/WorkbenchInputEditor.test.tsx` the panel, with no Preview
  Data picker and its note following the sample; `frontend/src/hooks/__tests__/useNodeDataCache.test.tsx`
  clearing a point that reads directly without the input-cache route; and
  `frontend/src/panels/__tests__/NodePanel.test.tsx` the panel chosen for each request input.
- The Workbench Output: `tests/test_extensions.py` covers the response's tables served beside
  the tables (`[]` without `response_tables`), `response_tables` true in the listing, a
  raising or misshapen `response_tables` answering 500 and tables breaking the v2 rules 422,
  and a `response_tables` without `quote_tables` or not a function refused.
  `tests/test_workbench_output.py` previews a Workbench Output's tables (through
  `execute_graph` too, on a saved pipeline with shared snapshots, a table at a time), each a dataframe
  typed as declared without the frames' other columns, filled by name, by a mapping's pick
  (two names swapped included) and with nulls where nothing fills a column; answers with
  their response through its generated code (`run()` and `score()`) and through deploy scoring
  (`output_fields` naming the response's fields) and `resolve_config`, the connections
  round-tripping as `target_port`; previews one typed row of nulls per one-row table before
  the workbench has a sample; says what to connect when nothing is connected, in the editor's
  preview too; fails each run
  failure with its message (no tables, a table with no connection, a mapping entry naming a
  column the frame lacks, a mapping entry for a column the table lacks, a column whose type
  does not fit, a one-row table of two rows or none, a request of two quotes); reads the tables'
  schemas without collecting; and refuses a second response node in save, `resolve_config` and
  `run()`.
  `tests/test_assistant_ops.py` covers the assistant's refusals and its wiring to a Workbench
  Output's tables, and `tests/test_config_io.py` writes its config file to
  `config/workbench_output/` and reads it back.
- In the frontend, `frontend/src/utils/__tests__/extensionQuoteTables.test.ts` covers
  `responseTablesPatch` (its mapping pruned with the tables), `paletteWorkbenchOutputConfig`
  and `workbenchOutputTableLabels`;
  `frontend/src/hooks/__tests__/useExtensionQuoteTables.test.tsx` the response's tables applied
  to a Workbench Output and never to a Quote Response;
  `frontend/src/utils/__tests__/connectionValidation.test.ts` the connections a Workbench
  Output refuses; `frontend/src/utils/__tests__/nodeUpdatePlan.test.ts` a connection kept while
  its table remains, wherever it moved, and removed with it, and
  `frontend/src/hooks/__tests__/useGraphCommitController.pending.test.ts` the warning that says so;
  `frontend/src/panels/__tests__/NodePalette.test.tsx` the palette's swap of the Quote Response
  and the response nodes' shared slot; `frontend/src/nodes/__tests__/PipelineNode.test.tsx` a
  target port per table; and `frontend/src/__tests__/editors/WorkbenchOutputEditor.test.tsx`
  the panel, with each table's connection and its mapping: a column filled by name, by a pick,
  or by nothing and flagged, and a pick sent as the new `mapping`.
