# Workbench — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_workbench_config.py` | The `[workbench]` table of `haute.toml`: `WORKBENCH_TOML_KEYS` (`enabled`, `form`), `DEFAULT_FORM_PATH` (`forms/form.json`), `WorkbenchError` (the base of every workbench problem the analyst fixes in the project), `WorkbenchConfigError`, the frozen `WorkbenchConfig` record (`enabled`, `form` as `haute.toml` names it, `project_root`, and `form_path`) and `read_workbench_config` (the table read and checked from the project root's `haute.toml`: disabled without the file or the table). |
| `src/haute/_workbench_form.py` | The form file's canonical shape, its reading and its revision: the pydantic models `FormSpec`, `FormSchema`, `SchemaTable`, `SchemaColumn`, `Page`, `TableInputWidget`, `CollectionWidget` and `FieldRef` (every field written, unknown fields refused), `WorkbenchFormError`, `FormDocument` (a form with its file's revision), `blank_form` (one blank sheet, named after the project), `render_form` (the file's text), `form_revision` (a file's revision: the content hash of its bytes), `write_form` (an atomic write, answering the written file's revision), `form_file_revision` (the file's revision as it is now, None when the file does not exist) and `read_form_document` (the file's bytes read once, hashed and checked, the blank form with no revision when the file does not exist, naming the file and what is wrong otherwise). |
| `src/haute/_workbench_tables.py` | The workbench's tables as the pipeline holds them: the models `WorkbenchTable` (`name`, `rows`, `columns`; `one_row`, `frame_schema`, `quote_dtype`) and `WorkbenchColumn` (`name`, `type`; `dtype`), `COLUMN_DTYPES`, `WorkbenchTablesError`, `parse_workbench_tables` (a node's `tables` config read strictly, naming the spot that is not as the workbench writes it), `check_tables` (every name held to `is_frame_label`, unique across tables whatever its case and within a table), `port_tables` (the tables with a column), `quote_schema` (the schema of one quote holding tables), `input_tables` and `output_tables` (the schema's tables of the role, in schema order, checked), `sample_quote` (the sample typed while building as one request holds it, each value as its column's type holds it) and `workbench_tables`, which gathers the three as a `WorkbenchTables`. |
| `src/haute/_workbench_input.py` | The Workbench Input's reading: `WorkbenchInputError`, `workbench_input_tables` (its ports from its config: the tables with a column, none refused saying what to add), `workbench_table_labels`, `read_quote` (each table's frame from a quote, typed as declared, a part that is not of its shape or a value that is not of its type refused naming the spot), `null_quote` (one quote with nothing filled in, one row of nulls per table), `workbench_table_frames` (its frames without a request: the sample read whole through `read_quote` as a request is, a many-row table it does not hold no rows and a one-row table its row of nulls, the `null_quote` while there is no sample, then cut to the demanded ports and columns) and `workbench_request_frames` (a deployed request's frames: its one quote, from the records as sent, read through `read_quote`). |
| `src/haute/_workbench_output.py` | The Workbench Output's tables and response: `WorkbenchOutputError`, `workbench_output_tables` (its tables read from its config through `parse_workbench_tables`, the ports those with a column, none refused saying what to add), `workbench_output_mapping` (its mapping read from its config, checked against the tables), `WorkbenchOutputTables` (its result: its tables' frames by name, carrying the tables they fill), `fill_workbench_tables` (the tables filled from the frames by port through the mapping, each column checked against and cast to its declared type, a one-row table checked for its one row when it is read), `workbench_response` (the response for one quote: each table under its name, built from the tables as a quote holds them) and `as_response` (a response node's result as the frame a request is answered with). |
| `src/haute/routes/workbench.py` | `router`, the workbench routes under `/api/workbench`: `workbench_status` (`GET /api/workbench`), `get_workbench_tables` (`GET /api/workbench/tables`) and `post_workbench_tables` (`POST /api/workbench/tables`, the same of a form in the request), both through `_tables_response` (the tables and the response tables checked as the pipeline takes them, a `WorkbenchTablesError` answered as the public contract 422), `get_workbench_form` (`GET /api/workbench/form`, the form with its revision) and `put_workbench_form` (`PUT /api/workbench/form`, through `_save_form`, which raises `StaleDocumentRevisionError` when the file's revision is not the base revision quoted, answered as the pipeline save answers it, else, under `save_lock`, writes the form and captures the written file on the save ledger through the pipeline save's capture, answering the form, its revision and the capture's commit, warnings and identity flag); each reads `haute.toml` through `_enabled_config` (404 while the workbench is not enabled), and all but the status the form, from the working directory on every request, off the event loop. |
| `frontend/src/api/workbench.ts` | `fetchWorkbenchStatus`: `GET /api/workbench` through the shared request machinery, validated by the generated `workbench` contract, whose validators load with the first response. `fetchWorkbenchTables`: `GET /api/workbench/tables`, validated by the same group's `WorkbenchTablesResponse`. `fetchWorkbenchFormTables`: `POST /api/workbench/tables` with a form, validated by `WorkbenchTablesResponse`. `fetchWorkbenchForm`: `GET /api/workbench/form`, validated by `WorkbenchFormResponse`. `saveWorkbenchForm`: `PUT /api/workbench/form` with the form and its `base_revision`, in one attempt (a retry of a save that landed but lost its answer would read as stale), validated by `WorkbenchFormSaveResponse`: the form at its new revision with the save's capture. |
| `frontend/src/stores/useWorkbenchStore.ts` | `useWorkbenchStore`: `enabled`, whether the project's workbench is, and `formPath`, where its form is as `haute.toml` names it; `load`, which fetches the status, leaves the store untouched while the workbench is not enabled (so the editor never re-renders for it) and reports a failure as one error toast; `activeView`, which view shows (`EditorView`: `pipeline` or `workbench`), and `showView`, which refuses the workbench's view while the workbench is not enabled; `tables`, the workbench's tables, sample and response tables from the newest fetch that succeeded; `refreshTables`, which numbers its fetches so that only the newest publishes them or toasts its failure; `awaitTables`, which settles with whether the newest fetch published (a superseded fetch taking the newer one's outcome); and `formDirty`, the form store's `dirty` mirrored by that store for the editor's navigation guards. |
| `frontend/e2e/workbench.spec.ts` | The workbench's journey in a browser against the real server (Playwright, Chromium): the workbench switched on for the end-to-end project and its sheets written, the scaffold's Quote Input and Quote Response replaced through `POST /api/pipeline/save` by a Workbench Input, a Transform and a Workbench Output, the view opened, the schema read, a Collection dragged out of the palette onto the sheet and given its fields, the sample typed and priced live, a quote priced in Preview apart from the sample, a column added to the schema, and one Save writing the component, the sample and the column to `forms/form.json` and then the pipeline, its Workbench Input's copy (`rating/config/workbench_input/quote.json`) carrying the column. |
| `frontend/src/utils/canonicalJson.ts` | `canonicalJson`, JSON with every object's keys sorted, so two values holding the same data compare equal whatever order their keys were written in: the form store's `dirty` and the copy patches' "already matches" both compare through it. |
| `frontend/src/utils/workbenchTables.ts` | The copy rules for a Workbench Input and a Workbench Output: `WorkbenchTables`, `PricedSample` (the sample priced: each output table's rows by its label), `quoteTablesPatch` (the update a Workbench Input's copy needs, `{tables, sample}`, both compared as JSON with object keys sorted and a missing sample read as `{}`, or null), `responseTablesPatch` (the update a Workbench Output's copy needs, `{tables}` compared the same way and `mapping` without the entries of tables and columns the new tables lack, or null), `paletteWorkbenchInputConfig` and `paletteWorkbenchOutputConfig` (what the palette's Workbench Input and Workbench Output start with: the newest tables, and sample, fetched), `readWorkbenchTables` (a copy's tables as the editor reads them, typed by the generated `WorkbenchTable`: each entry with a name, `one` or `many` rows and typed columns, any other left out), `workbenchTablePorts` (a workbench node's ports: its tables with a column, by name, in order, once), and `WORKBENCH_COPY_PATCHES` (the update rule for each workbench node type, `quoteTablesPatch` for a Workbench Input and `responseTablesPatch` for a Workbench Output). |
| `frontend/src/hooks/useWorkbenchTables.ts` | The hook the editor shell (`frontend/src/App.tsx`) calls to keep those copies current: a fetch when the workbench is enabled and on each `executionGeneration`, recorded against the generation; eligible tables applied once per Workbench Input and Workbench Output for each fetch, by `WORKBENCH_COPY_PATCHES`, through `onUpdateNode`, with an `isCurrent` that checks the generation and that no newer fetch has reached the node, while the document is editable and its top level shows; `nodeCreated` for a node a palette drop created; `bringUpToDate`, which fetches afresh, waits for that fetch (`awaitTables`) and applies it at once, resolving false when it failed, for the git flows' save; nothing while the document cannot change. |
| `frontend/src/panels/editors/WorkbenchInputEditor.tsx` | The Workbench Input's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Input: the tables read-only (`readWorkbenchTables`) with each name's `apiInputLabelIssue`, a note on what previews run on, chosen by whether the config's sample is a non-empty object, a note while the workbench is not enabled, and one while a submodel is open (`insideSubmodel`, passed down from `frontend/src/panels/NodePanel.tsx`). It exports `WorkbenchTableCard`, one table read-only (its name, its rows per quote and its columns, or `NO_COLUMNS_NOTE` for a table without any, which is no port), and `WorkbenchTablesHeader`, the section's title, its "Edit in Workbench" button while the workbench is enabled (`showView`) and its notes, which the Workbench Output's panel shares, and `WORKBENCH_DISABLED_NOTE`, the note's one wording. |
| `frontend/src/panels/editors/WorkbenchOutputEditor.tsx` | The Workbench Output's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Output: under `WorkbenchTablesHeader`, each table (`readWorkbenchTables`) with its name's `apiInputLabelIssue` against the document's reserved labels, as the Workbench Input's panel judges them, `NO_COLUMNS_NOTE` for a table without columns, else the node connected to its port (from the `targetHandle` of the panel's input sources) or "Not connected", and a row per column with a select of the connected frame's columns (the input source's `columns`) that fills it, chosen by `mappedSource`, through `onUpdate` with the new `mapping`: a pick kept by name, none kept as null, and a pick of the column's own name no entry, which fills by name. |
| `frontend/src/stores/useWorkbenchFormStore.ts` | `useWorkbenchFormStore`, the form while the view edits it: `form` and `revision` as read (`load`, once; `reload`, again, dropping edits and history), each read a turn in the store's queue, through `fetchWorkbenchForm`, with `status` and `loadError`; `change` (an edit that undo reverses, on the graph store's `appendHistoryEntry` and its history cap), `undo`, `redo`, `undoStack`, `redoStack`, `savedForm` and `dirty` (the form and the saved form compared through `canonicalJson`, whatever order the file writes their keys in; mirrored onto the workbench store's `formDirty` for the editor's guards); `save`, which takes its turn after the saves and syncs before it, writes the form as it stands through `saveWorkbenchForm` with the revision, adopts the new revision, toasts "Saved → <form>", reports the capture through `reportSaveCapture` (`uncaptured` set while the capture waited on a git identity or failed, every warning on a form save being the capture's), and fetches nothing itself, the project save it is the form step of fetching the tables afresh next; `flush`, the project save's form step: `save` while the form holds unsaved edits or an uncaptured save, else true without a request; `sync`, run after each adoption of the pipeline's document (`executionGeneration`, subscribed at module load) and after any save in flight: the file read again through `fetchWorkbenchForm`, nothing at the same revision, a changed file adopted with history dropped while the form is as saved, else `stale` set, and a failed read one error toast; `stale`, set by a save refused as `stale_document_revision` (one error toast, the edits kept) or by a sync that found the file changed under unsaved edits, and cleared by a reload; `saving`; and the gesture setters `pushSnapshot` (the form recorded for undo, the redo stack cleared) and `setFormRaw` (the form replaced without history, `dirty` recomputed), so a drag is one undo step. |
| `frontend/src/stores/useWorkbenchViewStore.ts` | `useWorkbenchViewStore`, the view's own state, neither saved nor undone: `section` (`sheets` or `schema` while building, or `preview`), `pageId` (null until chosen; `activePage` reads the form's first), `selectedId`, `zoom` (25% to 200%, `ZOOM_STEP` 10%), `fitted` (the sheets have been fitted since the form store last read a form; a zoom chosen since is kept), `creating` (a component being dragged out of the palette, with the pointer), `panelWidth` (360 to start, 320 to half the window), and the `sheet` and `viewport` elements; `showSection`, `showPage` (the sheets section, or Preview as before, nothing selected), `select`, `setZoom`, `zoomBy`, `fitZoom` (the viewport's width less the padding over the sheet's reach, through `sheetGeometry.fitZoom`, clamped through `setZoom`), `setCreating`, `setPanelWidth`, `setSheet` and `setViewport`. |
| `frontend/src/stores/useWorkbenchPricingStore.ts` | `useWorkbenchPricingStore`, the sample priced live: `pricer` (the view host's `Pricer`, null while the view is away), `price` (a `SamplePrice`: the tables, the values as the server typed them and the basis priced for), `error` (why the last pricing failed, until one succeeds) and `pricing`; `setPricer` (null also drops a scheduled pricing), `schedule` (`PRICING_DELAY_MS` after the last call) and `priceNow`, which runs one pricing at a time and once more for a request made meanwhile, an answer after the pricer changed dropped. `priceForm(form, pricer)`, the pricing itself (the form's tables through `fetchWorkbenchFormTables`, then the pricer, answering the tables with the typed sample as `PricedValues`), is Preview's too. |
| `frontend/src/stores/useWorkbenchPreviewStore.ts` | `useWorkbenchPreviewStore`, Preview's quote, apart from the sample and never saved or undone: `quote` (rows by table id, as typed), `checked` (Price has been pressed, so the cells that break a rule are marked), `price` (a `SamplePrice` for the quote), `error` (why there is no price, as the toolbar says it) and `pricing`; `setCell` (`withCell`), `setRows`, `clear`, and `priceQuote`, which does nothing while a pricing runs, the form is not read or the view has given no pricer, refuses a quote with nothing typed in the schema's current input tables (`quoteFilled`; `checked` set, `error` "Type the quote first"), else checks `quoteProblems` (any: `checked` set, `error` "N cells need attention", nothing sent) and prices through `priceForm` with the quote in the sample's place, keeping the price with `valuesBasis` of the schema and the quote, or "Pricing failed: …", unless Clear or a change of pricer came first, when the answer is dropped. |
| `frontend/src/workbench/priceSample.ts` | `priceSample(graph, workbench, source)`: the document's top-level nodes patched through `WORKBENCH_COPY_PATCHES` with the form's tables, sample and response tables, the Workbench Output found (none: an error naming what to add), and each of its ports (`workbenchTablePorts`) previewed through `previewNode` with `portLabel`, a failure rejecting with the server's reason or the preview's error; the document itself untouched. |
| `frontend/src/utils/sheetGeometry.ts` | Placing components on a sheet, in unzoomed pixels: `GRID` (8), `snap`, `moveRect` (snapped, kept off the top and left edges), `resizeRect` (by a `Handle`, the opposite edges kept, no smaller than a minimum), `placeAt`, `contentRight`, `sheetWidth` (the viewport's at the zoom, or wider to hold the components plus `SHEET_EDGE`, 32), `sheetHeight` (at least `MIN_SHEET_HEIGHT`, 800, with 240px of room below the lowest component), `fitZoom` (in steps of 5%, never past 100%), `SHEET_PADDING` (32) and `HANDLES`. |
| `frontend/src/utils/workbenchForm.ts` | Pure operations on the form, each returning a new form: `newId`, `uniqueName`, `readableName` and `allWidgets`; the sheets, `createPage` (named in turn), `addPage`, `renamePage` and `removePage` (never the last); the components, `findWidget`, `createWidget` (720 wide, a Table 200 high with 3 rows or a Collection 120 high with 3 columns, no fields, its keys in the file's order; a Table no smaller than 240 by 96, a Collection than 160 by 72, `widgetKinds.ts`), `addWidget`, `updateWidget` (a `WidgetPatch`, `rows` refused on a Collection and `columns` on a Table), `removeWidget` and `duplicateWidget` (16px below and to the right, a new id); what they show, `rowsShown`, `tableGrain`, `toggleField`, `moveField` and `shownFields` (each field with its column, or null); `widgetName` and `widgetProblems`, what stops a component showing what it should (no fields, a column gone, a table of the other kind, tables whose rows do not line up), each a `WidgetProblem` naming its component; the sample, `withCell`, `withSampleCell` (one cell, empty rows added up to it) and `withSampleRows` (several tables' rows as one edit); and `pricingBasis`, the schema and the sample as one string (`valuesBasis` of them, memoised per form); `createSchemaTable`, `createSchemaColumn` and `createIndexColumn` (`row_number`, an Integer, `index`); `addSchemaTable`, `updateSchemaTable`, `removeSchemaTable` (its columns taken out of the widgets that show them, its rows out of the sample), `setSchemaTableRows` (keys cleared and the index dropped for one row), `addSchemaColumn` (at an index), `moveSchemaColumn` (never the index, and nothing above it), `updateSchemaColumn`, `changeSchemaColumnType` (the range dropped for a column no longer a number, the allowed values for one that `takesOptions` no longer: True/false or Date) and `removeSchemaColumn` (taken out of the widgets and the sample); `fieldUses` and `fieldColumn`; `columnNameProblem` (an ASCII identifier that is not one of `PYTHON_KEYWORDS`, the server's `is_frame_label`); and `schemaProblems`, what would stop the schema being the pipeline's tables, each a `SchemaProblem` naming its table: a table's name through `apiInputLabelIssue` with the document's reserved labels, a column's name through `columnNameProblem`, unique in its table, a many-row table's key, an input column's range and allowed values, and allowed values on a column that cannot have them. `Widget` and `ColumnType` are derived from the generated form types. |
| `frontend/src/utils/sheetValues.ts` | The values typed into a sheet, the sample or a quote (`RowsByTable`): `cellKey`; `filled` and `rowFilled`, a value and a row as the server counts them (a tick or text other than blank, in any column but the index); `parseTypedNumber`, a number as the server types it, a currency sign, separators and spaces allowed; `cellProblem`, what breaks an input column's rules in a value (required, not a number, not a whole number, below `min`, above `max`, not one of the options; never a tick box or the index), `quoteFilled` (anything typed in the schema's current input tables, a value in a column or table since removed not counting, as the server leaves it out) and `quoteProblems`, the problems by cell over each input table's rows, a one-row table's one row always and a many-row table's filled rows only; `valuesBasis`, the schema and the values as one string; and `pricedRows`, a priced output table's rows (`PricedRows`) lined up with a grid's rows of an input table keyed alike, each filled grid row taking the next typed row and the output row whose key columns hold the same values as text. |
| `frontend/src/workbench/widgetKinds.ts` | `WIDGET_KINDS`, each kind of component's label, icon, hint, starting size and minimum; `PALETTE_KINDS`, what the palette offers; `COMPONENT_COLOR`, the entry colour. |
| `frontend/src/workbench/sheetInteractions.ts` | The pointer interactions on a sheet: `dropAt` (where a palette item released at a pointer position lands, through the view store's sheet and viewport, null off the viewport), `startCreate` (a palette item dragged onto the sheet, `creating` following the pointer, the component added and selected on release; a click adds nothing) and `startTransform` (a component moved, or resized by a handle: selected on press, the form snapshotted on the first move and replaced on each move through `setFormRaw`), each tracking the pointer on the window until it is released. |
| `frontend/src/workbench/SheetCanvas.tsx` | `SheetCanvas`, the showing sheet in its scrolling viewport: the viewport measured by a `ResizeObserver` and registered with the view store along with the sheet, the sheet sized by `sheetWidth` and `sheetHeight` and scaled by the zoom, the sheet's values given to the components through `SheetValuesContext` (`useSampleValues` while building, `usePreviewValues` in Preview), a `SheetWidget` per component with its `shownFields` and its first problem, editable and selectable while building alone, `DropGhost` where a component being dragged in would land while building, the sheet fitted on first show, and a press on the empty sheet deselecting. |
| `frontend/src/workbench/SheetWidget.tsx` | `SheetWidget`, one component on the sheet: its `WidgetBody`, inert but for its cells while `editable`, in a frame that selects it on press (`startTransform`, which blurs a field focused before so the sheet's keys reach the component) and on focus (`tabIndex` 0) and drags it, and grows eight `ResizeHandle`s once selected, and in Preview does nothing but scroll; the frame accented when selected, dashed in the warning colour naming a problem in its `title` and to a screen reader (`aria-describedby`). |
| `frontend/src/workbench/WidgetBody.tsx` | `WidgetBody`, a component on a sheet: its title, "Choose its fields from the schema in the panel on the right" with none, else `FieldBoxes` (a Collection's fields as labelled boxes, its columns across) or `FieldGrid` (a Table's fields as the columns of a grid of at least its rows, the index column numbering them, Add row and a delete per row across the input tables shown). Each input column's `Cell` is the control its type calls for (a checkbox, a select of the options, a date, else a `CommittedTextField`), holding the sheet's value from `useSheetValues` and changing it through its `setCell` or `setRows`, outlined with its problem as the title when the values' `problems` name it by `cellKey`; pressing one selects the component (`useTypeHere`). A label from its column with a star for a required input, a gone column "Missing column"; an output column shaded, showing the values' `price`: in a Collection its table's one row through `formatValue`, in a Table the row `pricedRows` lines up with the grid row through the first input table shown that is keyed alike (`tableGrain`), a dash for none, dimmed (`data-stale`) while `price.basis` is not the values' `basis`. |
| `frontend/src/workbench/PageTabs.tsx` | `PageTabs`, the sheets' tabs: one per sheet (`showPage`), the showing one marked; a plus adding a sheet (`createPage`, `addPage`); a double-click, or F2 on the tab, renaming one in a `CommittedTextField` (a blank name kept out); the showing sheet's cross deleting it (`removePage`) after `window.confirm` when components are on it, while another sheet remains; `readOnly`, in Preview, the tabs only switch sheets. |
| `frontend/src/workbench/PropertiesPanel.tsx` | `PropertiesPanel`, the selected component's panel on the kit's `SidePanel` (its width in the view store): its kind, Title (`CommittedTextField`), Layout (rows or columns in a `ValidatedTextField` holding the range) and Order (`FieldOrder`, a list of rows through `useListReorder` and Alt+Up/Alt+Down, each with a cross through `toggleField`), both once the component shows a field, and Fields (`TableFields` per schema table of the component's kind, collapsible, a checkbox per column through `toggleField`, greyed with its unticked columns disabled when `tableGrain` does not match the fields already chosen), or a pointer to the schema when there is no table of the kind. |
| `frontend/src/workbench/WorkbenchPalette.tsx` | `WorkbenchPalette`, the view's left column in the kit's palette shell: a `PaletteItem` per `PALETTE_KINDS` entry starting `startCreate` on press while building and disabled in Preview (titled "Build to lay out the sheets"), and the switcher under them; sharing `useUIStore.paletteOpen`, collapsed to the reveal strip (labelled "Show component palette") with the compact switcher. |
| `frontend/src/workbench/ViewSwitcher.tsx` | `ViewSwitcher`, the Pricing and Workbench buttons at the bottom of the left palette while the workbench is enabled (nothing otherwise), pressing `activeView` and calling `showView`; `compact` is the column of icon buttons beside the collapsed node palette. |
| `frontend/src/workbench/WorkbenchView.tsx` | `WorkbenchView`, the view over the area below the toolbar while it is active: given `resolveGraph` by the host, it sets the pricing store's pricer (`priceSample` on the resolved document with the settings store's active source) while mounted and schedules a pricing when the form's `pricingBasis` changes while the sheets show, and when the sheets show again; `WorkbenchPalette`, then the form read on first show (`load`), "Loading the workbench…", a read failure with Try again (`reload`), the stale banner with Reload, and once the form is read the section the view store names, `PageTabs` (read only in Preview) over `SheetCanvas`, or `SchemaEditor`; `useWorkbenchShortcuts` with the host's `onSaveShortcut`, the toolbar's Save; the lazy Git and Assistant panels as asides, else `PropertiesPanel` beside the sheets; and `DragChip`, which follows the pointer while a component is dragged out of the palette until the sheet's ghost takes over. |
| `frontend/src/workbench/WorkbenchToolbar.tsx` | `WorkbenchToolbar`, the toolbar while the view shows: `ToolbarBrand`; a `ToolbarColumn` of Build over Preview (`showSection` to `sheets` or `preview`); while building, a `ToolbarColumn` of Sheets over Schema and `UndoRedo` on the form store's history, or in Preview a `ToolbarColumn` of Price (the preview store's `priceQuote`, disabled while pricing, before the form is read or while nothing is typed in the schema's current tables, `quoteFilled`) over Clear (`clear` after `window.confirm`, disabled while nothing is typed); `ZoomInOut` (`zoomBy`) while the schema is not showing; the pricing store's `error` as "Pricing failed: …" while building, or the preview store's `error` in Preview; and `ProjectControls` with the host's `onSave` and `onCommit`, the pipeline toolbar's own Save and Commit, and its `editingDisabled`, so both are off, and the Assistant while no turn runs, exactly when the pipeline toolbar's are. |
| `frontend/src/workbench/SchemaEditor.tsx` | `SchemaEditor`, the Schema section: a block per table (its name in a `CommittedTextField`, role and rows selects, the column count with the table's problems, Delete with a confirmation when a sheet shows its columns, collapsible) with a row per column (the type marker, an `IconSelect` of `COLUMN_TYPE_OPTIONS`, or the fixed Integer of the index; the name, committing on blur or Enter, Enter adding the next column and Alt+Up and Alt+Down moving it; the rules summary; the key toggle in a many-row table; Label and rules opening `ColumnDetails`: the label and, for an input column, Required, a Min to Max range in `ValidatedTextField`s that refuse a non-number, and Allowed values; Remove with a confirmation when a sheet shows it), Add column and, for a many-row table, the index checkbox; then Add table. A new table's or column's name takes the focus and is selected. Problems come from `schemaProblems` with the document's `reserved_api_input_frame_labels`. |
| `frontend/src/workbench/columnTypes.ts` | `COLUMN_TYPES`, each column type's label, icon (the step editor's for the kind) and colour (`getDtypeColor` of its dtype), `COLUMN_TYPE_OPTIONS` for the marker, and `isNumeric`. |
| `frontend/src/workbench/useWorkbenchShortcuts.ts` | `useWorkbenchShortcuts`, the view's window-level shortcuts through `hooks/keyboardTargets.ts`: Ctrl/Cmd+S runs the host's save, `onSave` (a focused field blurred first), Ctrl/Cmd+Z undoes and Ctrl/Cmd+Shift+Z or Ctrl/Cmd+Y redoes outside a text field; while the sheets show and outside a control (`isFormControl`), Ctrl/Cmd+1 fits the sheet and, with a component selected, Escape deselects, Delete or Backspace removes, Ctrl/Cmd+D duplicates (`duplicateWidget`, the copy selected) and the arrows nudge by `GRID` or, with Shift, a pixel, a burst within 800ms one undo step through `pushSnapshot` and `setFormRaw`; in Preview, Ctrl/Cmd+S and Ctrl/Cmd+1 alone apply; keys in a modal dialog are left to it. |
| `frontend/src/workbench/useSheetValues.ts` | `SheetValues`, what a sheet's cells hold and show (`rows`, `setCell`, `setRows`, `price`, `basis` and `problems` by cell key), given to the components through `SheetValuesContext` and read with `useSheetValues`; `useSampleValues`, the form's sample edited through `change` with `withSampleCell` and `withSampleRows`, the pricing store's `price`, `pricingBasis` and no problems; `usePreviewValues`, the preview store's `quote`, `setCell` and `setRows`, its `price`, `valuesBasis` of the schema and the quote, and `quoteProblems` once `checked`. |

`src/haute/schemas.py` defines `WorkbenchStatusResponse`, `WorkbenchTablesResponse`,
`WorkbenchFormResponse`, `WorkbenchFormSaveRequest` and `WorkbenchFormTablesRequest`; `scripts/generate_api_contracts.py`
lists the four responses as the `workbench` response group, so the form's models are
generated types too; `frontend/src/api/types.ts` re-exports them.
`src/haute/routes/_error_handlers.py` answers a `WorkbenchError` as 409. `src/haute/deploy/_config.py` lists the `[workbench]` table,
with `WORKBENCH_TOML_KEYS`, in the `haute.toml` schema its whole-file check accepts.
`src/haute/_scaffold.py` renders the table in `haute_toml` when asked and the blank form as
`starter_form`; `src/haute/cli/_init_cmd.py` writes both for `haute init --workbench`;
`src/haute/_git_setup.py` seeds `forms/` into an unborn repository's root commit.

The view's host is in files other components own. `frontend/src/App.tsx`
([frontend-graph-canvas](../frontend-graph-canvas/low-level.md)) hides and fences the
pipeline editor while `activeView` is the workbench's, swaps the lazy `WorkbenchToolbar` for
`Toolbar`, renders the lazy `WorkbenchView` over the pipeline region with
`resolveWorkbenchGraph` (the whole document from its refs, through `resolveGraphFromRefs`),
gives both toolbars the same `requestSave` and `requestCommit` and the view `requestSave`
for Ctrl/Cmd+S, and ends the palette column with `ViewSwitcher` while the workbench is
enabled;
`frontend/src/hooks/useKeyboardShortcuts.ts` registers nothing while its `enabled` is false,
and `frontend/src/hooks/keyboardTargets.ts` holds the keystroke rules both shortcut hooks
share. `frontend/src/components/ProjectControls.tsx` and
`frontend/src/components/form/IconSelect.tsx` ([frontend-shared](../frontend-shared/low-level.md))
are the toolbar's right-hand group and the icon-over-select control the view reuses;
`frontend/src/hooks/useListReorder.ts` (frontend-shared) is the drag-reorder hook the fields'
order shares with the step editor's cards; `frontend/src/haute-ui/PaletteShell.tsx`'s reveal
strip takes the label the component palette gives it; and
`frontend/src/stores/useGraphStore.ts` exports `appendHistoryEntry`, the one history rule.

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
Workbench Input reads none. Its tables are `haute._workbench_tables`' and its reading
`haute._workbench_input`'s: its frames without a request, read from its sample, come from
`workbench_table_frames`, through `resolve_workbench_input_from_config` in
`src/haute/_node_apply.py`; its ports from `workbench_table_labels`, which reads the tables
alone; a deployed request's frames from `workbench_request_frames`, which
`_score_graph_lazy` in `src/haute/deploy/_scorer.py` injects; and its request's schema, for
deploy, from `quote_schema` of its ports. The generic readers of a request input's tables
each decide by type: `_declared_api_input_port_columns` in `src/haute/projection.py`,
`_declared_api_input_frame_schema_items` in `src/haute/_execute_lazy.py`,
`_validator_issues` in `src/haute/_node_config_recovery.py`, `_resolve_api_input_table` in
`src/haute/_data_points.py`, `recoverable_request_input_source_handles` in
`src/haute/_editor_identities.py` (recovery and the document's identities), the test-quote
scoring in `src/haute/deploy/_validators.py` and the table-name clash check in
`src/haute/routes/_save_pipeline.py`. `src/haute/_graph_utils.py`, which
`haute._types` imports, holds the same set as plain values, `REQUEST_INPUT_KINDS`, and
`is_frame_label`, the one rule a Quote Input's table labels, a workbench table's and
column's names and the editor's source handles follow.
`src/haute/_graph_shape.py` holds `SINGLETON_NODE_GROUPS` and `validate_singleton_groups`,
which save (`src/haute/routes/_save_pipeline.py`) and deploy (`resolve_config` in
`src/haute/deploy/_config.py`) enforce and the assistant's capability manifest
(`src/haute/assistant/_catalog.py`) reads; `src/haute/assistant/_ops.py` refuses to author a
Workbench Input, and its node card is `src/haute/assistant/assets/node_cards/workbenchInput.json`.
A Workbench Input makes no input-cache request: `inputSnapshotSource`
(`frontend/src/utils/inputSnapshotSource.ts`) gives a snapshot source only to a request input
with a structured `path`. In the editor, `frontend/src/utils/nodeTypes.ts` holds the Workbench
Input's `NODE_TYPE_META` entry, `REQUEST_INPUT_TYPES`, `isRequestInputType`,
`singletonTypesOccupiedBy` and `singletonLimitMessage`, and every request-input check uses
`isRequestInputType`; `frontend/src/panels/NodePalette.tsx` shows the Workbench Input in the
Quote Input's place while the workbench is enabled and drags it with
`paletteWorkbenchInputConfig`; `requestInputFrameLabels` and `requestInputFrameColumns` in
`frontend/src/utils/apiInputPorts.ts` derive a request input's frames and their columns by
its type (a Workbench Input's through `workbenchInputFrameLabels`: `workbenchTablePorts`
judged as a Quote Input's labels are), and `applyApiInputConfigChange` there takes the
node's type, following names for a Workbench Input; `onUpdateNode` in
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
shows it in the Quote Response's place while the workbench is enabled and drags it with
`paletteWorkbenchOutputConfig`; `validatePipelineConnection` in
`frontend/src/utils/connectionValidation.ts` takes a connection to it only on a table's port;
`prepareNodeUpdate` in `frontend/src/utils/nodeUpdatePlan.ts` removes the connections whose port
its new tables lack, which `useGraphCommitController` reports; and `InputSource` in
`frontend/src/panels/editors/_shared.tsx` carries each input's `targetHandle` for its panel.

## Key types and data structures

- **`WorkbenchConfig`** (`src/haute/_workbench_config.py`): `enabled`, `form` (the path as
  written in `haute.toml`, or `DEFAULT_FORM_PATH`), `project_root`, and `form_path`
  (`project_root / form`). `read_workbench_config(project_root)` returns a disabled config
  when `haute.toml` or its `[workbench]` table is absent, and raises `WorkbenchConfigError`
  (a `WorkbenchError` and a `ConfigError`) when the file cannot be read, is not UTF-8 text or
  cannot be parsed, the table is not a table, holds a key outside `WORKBENCH_TOML_KEYS`, lacks
  `enabled`, gives `enabled` a non-boolean, or gives `form` anything but a non-empty relative
  path whose resolution stays inside the project root; the default path is resolved and checked
  the same way, so a `forms` symlink or junction leading outside the project is refused.
- **`FormSpec`** (`src/haute/_workbench_form.py`): `version` (the literal 1), `name`,
  `data_schema` (written and read as `schema`; a `Schema` of `tables`), `pages` (at least
  one `Page`: `id`, `title`, `widgets`) and `sample` (`dict[str, list[dict[str, str | bool]]]`,
  by table id then column id, `{}` for none). A `SchemaTable` has `id`, `name`, `role`
  (`input` or `output`), `rows` (`one` or `many`) and `columns`; a `SchemaColumn` has `id`,
  `name`, `type` (`int`, `float`, `str`, `bool` or `date`), `label`, `key`, `required`, `min`,
  `max`, `options` and `index`. A widget is a `TableInputWidget` (`type` `tableInput`, `title`,
  `fields`, `rows` 1–50) or a `CollectionWidget` (`type` `collection`, `title`, `fields`,
  `columns` 1–12), each with `id`, `x`, `y` (0 or more), `w`, `h` (1 or more); a `FieldRef`
  names a `table` and a `column` by id. Every model forbids unknown fields, and
  `model_dump(mode="json")` writes every field, defaults included.
- **`WorkbenchTable`** and **`WorkbenchColumn`** (`src/haute/_workbench_tables.py`): the
  pydantic models of a workbench node's tables, unknown fields refused: a table is `name`,
  `rows` (`one` or `many`) and `columns`, a column `name` and `type` (`int`, `float`, `str`,
  `bool` or `date`); `one_row`, `frame_schema` (the columns' dtypes, `COLUMN_DTYPES`, held
  equal to the Quote Input's) and `quote_dtype` (a `pl.Struct` of them, in a `pl.List` for a
  many-row table) read off a table. `parse_workbench_tables(value, owner=...)` reads a
  config's list strictly and `check_tables` holds every name to `is_frame_label`, unique
  across tables whatever its case and within a table; `port_tables` are the tables with a
  column and `quote_schema` the schema of one quote holding tables. **`WorkbenchTables`**:
  `tables`, `sample` and `response_tables`, as `GET /api/workbench/tables` serves them; the
  generated contract types the tables as `WorkbenchTable`, which `frontend/src/api/types.ts`
  re-exports.
- **`WorkbenchStatusResponse`** (`src/haute/schemas.py`): `enabled` and `form` (the path as
  `haute.toml` names it while enabled, else `null`). **`WorkbenchTablesResponse`**: `tables`,
  `sample` (an object, `{}` for none) and `response_tables` (`[]` for none).
- **Store state** (`frontend/src/stores/useWorkbenchStore.ts`): `enabled` (false until a
  status says otherwise), `formPath`, `activeView` (an `EditorView`: `"pipeline"` or
  `"workbench"`) and `tables` (`WorkbenchTables | null`: `tables`, `sample`,
  `responseTables` and `fetch`, the fetch's number).
- **`FormDocument`** (`src/haute/_workbench_form.py`): `spec` and `revision` (`str | None`).
  **`WorkbenchFormResponse`** (`src/haute/schemas.py`): `form` (a `FormSpec`) and
  `revision` (the file's content hash, `null` while the form has never been saved).
  **`WorkbenchFormSaveRequest`**: `form` and `base_revision` (`null` for a first save).
  **`WorkbenchFormSaveResponse`**: the response with the save's `warnings`, `git_sha` and
  `identity_required`, as `SavePipelineResponse` has them.
- **Form store state** (`frontend/src/stores/useWorkbenchFormStore.ts`): `form`, `revision`,
  `status` (`idle`, `loading`, `ready` or `failed`), `loadError`, `savedForm` (the form as
  read or last saved, serialised), `dirty`, `undoStack`, `redoStack`, `stale`, `saving` and
  `uncaptured` (the last save was written but not captured on the ledger, for want of a git
  identity or because the capture failed).
- **`SampleValue`** and **`SampleRow`** (`frontend/src/utils/workbenchForm.ts`): text or a
  tick, and a row of them keyed by column id. **`Pricer`**, **`PricedValues`** and
  **`SamplePrice`** (`frontend/src/stores/useWorkbenchPricingStore.ts`): a function of a
  `WorkbenchTablesResponse` to a `PricedSample`; a `PricedSample` with `sample`, the values
  priced as the server typed them; and that with its `basis`. **`RowsByTable`** and
  **`PricedRows`** (`frontend/src/utils/sheetValues.ts`): rows by table id, and a priced
  table's rows. **`SheetValues`** (`frontend/src/workbench/useSheetValues.ts`): `rows`,
  `setCell`, `setRows`, `price`, `basis` and `problems`, a message by cell key.
  **Preview store state** (`frontend/src/stores/useWorkbenchPreviewStore.ts`): `quote`,
  `checked`, `price`, `error` and `pricing`.
- **`SchemaProblem`** (`frontend/src/utils/workbenchForm.ts`): `tableId` and `message`.
  **`WidgetProblem`**: `widgetId` and `message`. **`WidgetPatch`**: a partial `Rect` with
  `title`, `fields`, `rows` (a Table's) or `columns` (a Collection's). **`ShownField`**:
  `key` and `found`, the field's table and column or null.
- **`Rect`** and **`Handle`** (`frontend/src/utils/sheetGeometry.ts`): `x`, `y`, `w`, `h` in
  unzoomed pixels; one of the eight handles, `n`, `s`, `e`, `w` and their corners.
- **View store state** (`frontend/src/stores/useWorkbenchViewStore.ts`): `section`, `pageId`,
  `selectedId`, `zoom`, `creating` (`type`, `clientX`, `clientY`, or null), `panelWidth`,
  `sheet` and `viewport`. **`WIDGET_KINDS`** (`frontend/src/workbench/widgetKinds.ts`): per
  kind, `label`, `icon`, `hint`, `size` and `min`.
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
  editor's twin. **`WorkbenchOutputConfig`**: `tables`, the response's tables in the
  Workbench Input's shape, and `mapping`, `dict[str, dict[str, str | None]]` by table name and
  column name: a frame column's name, or `None` for none; a column without an entry is
  filled by name.
- **A Workbench Output's tables** (`src/haute/_workbench_output.py`): `WorkbenchTable`s read
  by `parse_workbench_tables`, the ports those with a column. A column's declared type
  admits a frame's column of that dtype or `pl.Null`, and also any integer dtype for `int`,
  any integer, float or decimal dtype for `float`, and `pl.Categorical` or `pl.Enum` for
  `str`; the column is cast to the declared dtype (`COLUMN_DTYPES`).
- **`assemble_workbench_output_from_config(*dfs, config, base_dir=None, ports=None)`**
  (`src/haute/_node_apply.py`): `dfs` are the incoming frames in edge order and `ports` the
  target port of each, aligned; without `ports` it refuses to run. It returns
  `WorkbenchOutputTables`, a `dict` of lazy frames by table name with the tables as `.tables`.

## Control flow

1. **Status.** The editor shell calls `useWorkbenchStore.load()` once. `GET /api/workbench`
   reads `read_workbench_config(Path.cwd())` in the thread pool and answers `enabled` and
   `form`. An enabled status is stored; a disabled one changes nothing; a failure shows an
   error toast naming the cause and the store stays as it started.
2. **The tables, served.** `GET /api/workbench/tables` reads the config the same way, answers
   404 while it is disabled, reads the form with `read_form_document` in the thread pool (through `_saved_tables_response`), builds
   `workbench_tables(spec)`, whose `input_tables` and `output_tables` check their names with
   `check_tables`, answering a `WorkbenchTablesError` as the public contract 422 through
   `contract_error_http_exception`, and answers `WorkbenchTablesResponse` with the tables,
   the sample (unchecked) and the response tables. `input_tables` and `output_tables` make a
   `WorkbenchTable` per schema table of the role, in schema order: its name, its rows per
   quote and its columns, each its name and type, the index column an `int`. `sample_quote`
   walks the input tables: for each, the sample's rows (one for a one-row table), each row with
   values typed, a record left out when nothing is typed in it, the index set to the row's
   number as the rows stand, a non-empty string coerced for an `int` or `float` column
   (currency signs, thousands separators and spaces stripped; text that is not a finite number
   kept as typed), a ticked box `True`, an unticked box in a filled `bool` row `False`; a table
   with no records is left out.
3. **Fetched.** `useWorkbenchTables` calls `refreshTables()` when the workbench is enabled and
   whenever `executionGeneration` advances, which it does on every document adoption and never
   on a save's acknowledgement, and records the fetch's number against the generation. Each
   fetch has a number, and only the newest settles: into `tables`, or into one error toast that
   leaves `tables` as it was.
4. **Applied.** The tables are eligible when `tables.fetch` is at least the number recorded for
   the current generation. While they are, `editingReadOnly` is false and the view stack has
   one entry, the hook calls `onUpdateNode` for each Workbench Input on the canvas that has not
   had this fetch, with `quoteTablesPatch` applied (none when the copy matches) and an
   `isCurrent` that checks the generation, and for each Workbench Output with
   `responseTablesPatch` (none when the copy matches). The commit controller checks `isCurrent`
   before committing, again after identity resolution, and `prepareNodeUpdate` runs
   `applyApiInputConfigChange` with the node's type, which for a Workbench Input keeps only
   the connections whose tables' names remain ports and reports the rest, and for a Workbench
   Output keeps each connection whose `targetHandle` is one of the new tables' ports
   (`workbenchTablePorts`) and removes the rest, which
   the commit controller reports in one warning toast as connections whose tables no longer
   exist. The hook reads the nodes through `App`'s graph ref, so graph edits, undo and redo
   never run it. When a palette drop creates a node, `onDrop` reports it to `onNodeCreated`,
   the hook's `nodeCreated`, which applies the eligible tables to that node once it is on the
   canvas.
5. **Shown.** While the workbench is enabled, `NodePalette` renders the Workbench Input in the
   Quote Input's place in `PALETTE_TYPES` and drags it with `paletteWorkbenchInputConfig`: the
   latest tables and sample, or `[]` and `{}` before the first fetch; and the Workbench Output
   in the Quote Response's place, dragged with `paletteWorkbenchOutputConfig`: the latest
   response tables, or `[]`. `NodeConfigEditor` renders `WorkbenchInputEditor` and
   `WorkbenchOutputEditor` for them.
6. **One request input.** Save's `_validate_singletons` and `resolve_config`, before
   `prune_for_deploy`, call `validate_singleton_groups` on the flattened pipeline. In the
   editor, `singletonTypesInDocument`, the drop check and the paste check count occupied
   singleton types through `singletonTypesOccupiedBy`, which gives both request inputs for
   either, and a refused drop toasts `singletonLimitMessage`.
7. **Frames without a request.** `_build_api_input` gives a Workbench Input a source that
   calls `resolve_workbench_input_from_config` with the ports' demanded columns, and the
   standalone runner in `src/haute/_standalone_nodes.py` calls it too. The resolver loads the
   config as `resolve_api_input_from_config` does. `workbench_table_frames` reads the ports
   with `workbench_input_tables` (`parse_workbench_tables`, then `port_tables`; no table, or
   none with a column, raises `WorkbenchInputError` saying what to add). With no sample
   (absent, `None` or `{}`) each port is a one-row `LazyFrame` of its `frame_schema` and null
   values. With one, it reads the sample whole before it projects, through `read_quote`: the
   sample must be a `dict`; each one-row table's part a `dict` or `None` (its one row, of
   nulls when `None`), each many-row table's a `list` or `None` (its rows, none when `None`)
   whose entries are `dict`s; each declared column's value is read by `_value` as its type
   holds it (`str`: a `str`; `bool`: a `bool`; `int`: an `int` that is not a `bool`; `float`:
   an `int` or finite `float` that is not a `bool`, as a `float`; `date`: a `YYYY-MM-DD`
   string `date.fromisoformat` accepts), `None` staying null, and a frame is built per table
   with `pl.DataFrame(columns, schema=frame_schema)`. A refusal names the spot
   (`policy.limit is 'lots', not 'int'`, `items[1] is a value, not a row`, `the sample is a
   list, not an object`), and `workbench_table_frames` raises it as `WorkbenchInputError`
   "The workbench's sample does not fit this Workbench Input's tables: <reason>. Correct it
   in the workbench and save it.", a public contract error, so a preview in its worker
   answers 422 with it. Each port is then its frame cut to the demanded columns
   (`_demanded`: every port whole without a demand; an unknown port or column a
   `ValueError`; an empty demand kept by one carrier column), the null quote's one-row frames
   while there is no sample. `snapshot_backed_inputs`
   leaves the node out, since it has no structured path; `own_facts` in
   `src/haute/_seed_plans.py` reports it cheap and slice-transparent, and the bounded-admission
   check admits it without a read; `api_input_port_metadata` in `src/haute/_ram_estimate.py`
   reads `workbench_table_labels` first, answering no metadata when the tables fail or lack
   the port, then sizes the port from its frame through `_detailed_dataframe_metadata`,
   letting a sample's misfit through.
8. **Its table points.** In `src/haute/_data_points.py` a Workbench Input's table point keeps
   the `api_input_table` kind but `_resolve_workbench_table` resolves it current at once from
   `workbench_table_labels`, never the sample, versioned by its tables, its sample and its
   lineage, with no input identity, so `src/haute/routes/_node_data_service.py` reports it as
   reading directly. Leasing it runs `workbench_table_frames` for the port and projects the
   demand; running it answers "A Workbench Input's tables are read directly; there is nothing
   to cache."; `clearDelegatedData` in `frontend/src/hooks/useNodeDataCache.ts` clears nothing
   for a point that reads directly. For the cache report, `api_input_table_labels` takes the
   labels from `workbench_table_labels` and `api_input_table_digests` gives none, so
   `points_for_graph` reports the tables together as one row that reads directly.
9. **Deploy.** Deploy reads a Workbench Input's tables, never its sample. For a Workbench
   Input, `infer_input_schema` in `src/haute/deploy/_schema.py` returns `quote_schema` of its
   ports as dtype strings, through `_workbench_tables` (a `WorkbenchTablesError` becoming a
   `ValueError` naming the node), and `_read_sample_row`, which execution-policy planning and
   the output-schema dry run share, returns `null_quote` of them, one quote with nothing
   filled in (each one-row table an object of nulls, each many-row table a list of one row of
   nulls), typed by that schema. When the dry run falls back to the hard-capped batch worker,
   `_capped_worker_output_schema` passes the sample's schema to `prepare_batch_scoring` as
   `input_schema`, carried on `BatchScoreRequest`, and the worker builds its input frame with
   it, so the quote of nulls keeps its types; a served request passes none. Where
   `_score_graph_lazy` injects the live frame as a Quote Input's output, for a Workbench Input
   it reads the request once, at planning, through `workbench_request_frames`: the records of
   a `QuoteRequest` (`src/haute/deploy/_scorer.py`), which `/quote`, the batch worker and
   `score_test_quotes` hand over as the quotes were sent and MLflow's `predict` as the
   records its signature, a map of anything per table, let through (`_request` in
   `src/haute/deploy/_model_code.py`), or `to_dicts()` of a frame, refused unless exactly one, read
   through `read_quote` into a frame per port, which the memory estimate takes as the
   per-port frames of `runtime_source_frames_by_node` so each consumer is sized from its
   table, a refusal
   raised as "The request does not fit this Workbench Input's tables: <reason>."; the walk
   hands each edge its port's frame by its handle, as it does a multi-frame source's.
   `score_test_quotes` in `src/haute/deploy/_validators.py` therefore scores a Workbench
   Input's test-quote cases one request each and checks each against its own request's one
   row, reported by its row in the file (`_validate_expected_outputs_per_case`; a case
   answered with other than one row fails on its own), where a Quote Input's cases are one
   request.
   Recovery and the document's identities take a Workbench Input's source handles from
   `recoverable_request_input_source_handles` in `src/haute/_editor_identities.py`, which
   reads a copy as the editor does (`recoverable_workbench_source_handles`: a table with a
   name, `one` or `many` rows and a column of a known type, its name a handle by
   `is_frame_label`, once whatever its case) and refuses nothing.
10. **Connected.** `PipelineNode` renders a Workbench Output's body as `FramePortRows` with
    `direction="target"`, one row per port from `workbenchTablePorts`, each a target
    `Handle` whose id is the table's name, or "No tables" with none; it has no default input
    and no source handle, and the ports join the signature that re-measures the node's
    handles.
    `validatePipelineConnection` refuses a connection into a Workbench Output whose target
    handle is none of its tables ("Connect to one of <node>'s tables"), one to a table that
    already has a connection ("<table> is already filled by <node>"), and one from a node that
    already fills another of its tables ("<source> already fills <table>; a node fills one
    table"), before the shared input-name check. `onConnect` keeps the handle as the edge's
    `targetHandle`, and save and codegen write it as `target_port`.
11. **Run.** `_build_workbench_output` builds a function of the incoming frames that calls
    `assemble_workbench_output_from_config` with the config and `ports=ctx.target_handles`. It
    is registered opaque, so projection asks its parents for their whole frames: the mapping may
    pick any of their columns, and a mapping entry naming a column a frame lacks reaches the
    node, to be named, rather than failing a projection contract upstream. With nothing
    connected it reports itself a source, so the walk calls it with no frames and it says what
    to connect. The standalone runner calls it with the node's sidecar and `target_ports`:
    `Pipeline._execute_transform` passes the target port of each incoming `pipeline.connect`,
    through `Node._invoke` and `run_configured_node`. The function pairs each frame with its
    port, raising when a port is missing, is none of the tables' names or repeats one, reads
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
12. **Its tables, previewed.** The walk keeps the result as a multi-frame bundle, as it keeps
    a Workbench Input's, so the preview of a Workbench Output with several tables shows the
    table named by the request's `port_label`, its `frame_columns` listing every table for the
    preview's table picker, and one with a single table shows that table.
    `previewPortLabel` in `frontend/src/hooks/usePipelineAPI.ts` asks for the first of
    `workbenchTablePorts`. `is_node_output` in `src/haute/_seed_plans.py` never makes a
    Workbench Output a node-output snapshot point, as it never makes a request input one: a
    bundle is not one frame, so a preview with shared snapshots never captures it, though two
    inputs make it a join point. The executor renders only a Quote Response's preview with
    `render_output_document`. `execute_trace` in `src/haute/trace.py` refuses a Workbench
    Output's bundle as it refuses any multi-frame target, saying to trace the node connected to
    the table instead.
13. **The response node.** `Pipeline._resolve_output_node` returns the one node whose
    `_node_type` is in `RESPONSE_NODE_TYPES`, raising `ExecutionError` naming them when there
    are several; `find_output_node` picks the response node the same way; trace correlation
    carries values through a response node unchanged. Where the pipeline answers a request,
    `as_response` turns the response node's result into the frame it answers with:
    `Pipeline.run` and `Pipeline.score` before collecting, and `_score_graph_lazy` in
    `src/haute/deploy/_scorer.py`, which a deployed pipeline's `/quote`, test quotes, the
    output-schema dry run and batch scoring share, before selecting `output_fields`. For a
    Workbench Output it calls `workbench_response`: the response's schema is `quote_schema`
    of its tables, and a `limited_python_scan` whose producer collects each table through
    `execution_collect` (a one-row table's scan having checked its one row), takes a one-row
    table's row as an object and a many-row table's rows as a list of objects, and builds the
    one-row response with `pl.DataFrame([document], schema=...)`. A Quote Response's result is
    its document already, so `_quote_response_content` renders either unchanged.
14. **One response node.** `validate_singleton_groups` counts both response node types as one
    group. In the editor, `singletonTypesOccupiedBy` gives both for either, so the palette greys
    out the one it offers and the drop and paste checks refuse a second, with
    `singletonLimitMessage` naming "Quote Response or Workbench Output".
15. **Scaffolding.** `handle_init` with `workbench` set passes it to `haute_toml`, which appends
    the `[workbench]` table (`enabled = true`, `form = "forms/form.json"`) after `[ci.staging]`,
    and writes `starter_form(name)`, the text of `blank_form(name)` (one sheet, "Sheet 1", no
    tables, no sample), to `forms/form.json`, reporting both in its summary. `_SEED_PATHSPECS`
    carries `forms/`, so `set_working_branch`'s unborn-repository seed stages the form with the
    pipeline's files.
16. **The form, read.** `GET /api/workbench/form` reads the config through `_enabled_config`
    (404 while disabled) and `read_form_document` in the thread pool, which reads the file's
    bytes once, hashes them (`form_revision`, `content_hash_bytes`) and parses them, or
    answers the blank form named after the project folder with no revision when the file
    does not exist, as `WorkbenchFormResponse`.
17. **The view.** `useWorkbenchStore.showView("workbench")` (the switcher, or a panel's
    "Edit in Workbench") sets `activeView`. `App` reads it as `pipelineActive`: the pipeline
    region (`data-testid="pipeline-view"`) gets `invisible` and `inert`,
    `useKeyboardShortcuts` is given `enabled: false` and registers nothing, the Ctrl/Cmd+Enter
    effect returns early, React Flow's `deleteKeyCode` and `panActivationKeyCode` become
    null, a store subscription closes the context and connection-drop menus on leaving,
    `WorkbenchToolbar` replaces `Toolbar` and `WorkbenchView` renders over the region, both
    lazily. The view calls `useWorkbenchFormStore.load()`, which fetches the form unless
    `status` has left `idle`, and registers `useWorkbenchShortcuts` with the host's
    `requestSave`.
18. **Edited.** The schema editor calls `change(update)` with a pure operation from
    `utils/workbenchForm.ts`; the store keeps the previous form on `undoStack`
    (`appendHistoryEntry`), clears `redoStack` and recomputes `dirty` against `savedForm`;
    `undo` and `redo` move forms between the stacks. Problems are recomputed from the form
    on each render with the document's reserved labels.
19. **Saved.** Save in either toolbar, and Ctrl/Cmd+S in either view, call the host's
    `requestSave` (`App.tsx`): the git readiness gate, then `saveProject`, the project's
    save (item 27), whose form step is the store's `save`. `save` takes its turn after the
    saves and syncs before it and PUTs the form with `revision` as `base_revision`. `_save_form`, under `save_lock`, reads `form_file_revision` and refuses
    with `StaleDocumentRevisionError` when it is not `base_revision`; else `write_form`
    writes the canonical text and `capture_save_in_ledger` (`haute.routes._save_pipeline`,
    the pipeline save's capture, given the file's path relative to the project) commits
    the file on the ledger when the clone has a working branch, and the response answers
    the new revision with the capture's `git_sha`, `warnings` and `identity_required`. The
    store adopts the revision, setting `savedForm`, recomputing `dirty` (edits made
    meanwhile stay unsaved) and `uncaptured` (from `identity_required`), toasts the save,
    reports the capture through `reportSaveCapture` (the commit on the branch indicator,
    the history nonce, each warning toasted, the identity prompt unless dismissed) and
    fetches nothing itself: the project save fetches the tables afresh next, through
    `bringUpToDate` (item 27), the one fetch that reaches the Workbench Input and Workbench
    Output through `useWorkbenchTables` before the pipeline is saved; a fetch of the form
    save's own would publish a second update of each node, and the pipeline's save waits
    for every update, a superseded one counting as a failure. A 409 whose detail
    starts with `stale_document_revision` sets `stale` and toasts; `reload` reads the form
    again and clears it.
20. **The sheets, shown.** With the view's `section` at `sheets` or `preview`,
    `WorkbenchView` renders `PageTabs` over `SheetCanvas`, which registers its viewport and
    sheet with the view store, measures the viewport, fits the zoom once, gives the
    components the sheet's values (`useSampleValues` or `usePreviewValues` through
    `SheetValuesContext`), and draws `activePage(form, pageId)`'s components, each
    `SheetWidget` with its `shownFields` and the first of its `widgetProblems`. The
    toolbar's Sheets and Schema call `showSection`, which clears the selection; `showPage`
    from a tab shows that sheet, in Preview as before.
21. **A component added.** `startCreate` on a palette item tracks the pointer, publishing
    `creating` on each move: `DragChip` follows it while `dropAt` finds no place, and
    `DropGhost` draws the place once it does. Released on the sheet, the item becomes
    `createWidget(type, rect)` added to the active page through `change` and selected;
    released elsewhere, or without moving, nothing is added.
22. **Moved and resized.** `startTransform` on a frame or a handle selects the component
    and tracks the pointer: the first move calls `pushSnapshot`, and each move replaces the
    form through `setFormRaw` with `moveRect` or `resizeRect` of the original rect by the
    pointer's travel divided by the zoom, so the gesture is one undo step. The keyboard's
    nudges do the same, one snapshot per burst.
23. **Properties.** `PropertiesPanel` finds the selected component in the form and edits it
    through `change` with `updateWidget`, `moveField` and `toggleField`; the fields offered
    are the schema tables whose `rows` match `rowsShown(widget)`, each usable when its
    `tableGrain` matches the grain of the first field already shown.
24. **The sample, typed.** A `Cell` commits a value through the sheet's values' `setCell`,
    while building `change` with `withSampleCell(spec, tableId, row, columnId, value)`, row
    0 for a Collection's one-row tables and the grid's row otherwise; Add row and a delete
    call `setRows`, while building `withSampleRows`, with every input table the grid shows,
    so one edit changes them all.
25. **Priced.** The view sets the pricing store's pricer on mount and clears it on unmount,
    and calls `schedule` when `pricingBasis(form)` changes while the sheets show, and when
    the sheets show again. After
    `PRICING_DELAY_MS`, `priceNow` runs one pricing: `fetchWorkbenchFormTables(form)`
    (`POST /api/workbench/tables`, `_tables_response` on the posted form), then the pricer,
    `priceSample(resolveGraph(), workbench, activeSource)`, which patches the top-level
    workbench nodes through `WORKBENCH_COPY_PATCHES` and previews the Workbench Output per
    port (both through `priceForm`); the store keeps `{basis, tables, sample}`, the
    sample as the server typed it, or the reason. A request made while one runs sets a
    flag, and the run prices once more when it finishes.
26. **Shown.** `FieldBoxes` reads the values' `price` and shows
    `price.tables[table.name][0][column.name]` for an output column; `FieldGrid` shows, for
    a many-row output column, the row `pricedRows` lines up with each grid row (the typed
    rows from `price.sample[input.name]`, the output rows from `price.tables[output.name]`,
    the input table the first shown with the output table's `tableGrain`); both dimmed when
    `price.basis` is not the values' `basis`; `WorkbenchToolbar` shows `error`.
27. **The project saved, and committed.** `saveProject` (`App.tsx`) is the one save of the
    project: from Save and Ctrl/Cmd+S through `requestSave` and from Commit through
    `requestCommit`, both behind the git readiness gate (the working-branch or divergence
    modal, whose confirmation runs the pending save or commit), and from a move's Save
    first, the Git panel's save before a switch and the identity prompt's retry directly.
    It refuses up front, before the form is saved, a document whose `can_save` is false or
    whose graph is not synchronised (`documentReadOnlyReason`, the pipeline save's own
    refusal) and a save asked for inside a submodel; then, while the workbench is enabled,
    saves the form through the form store's `flush` (the store imported lazily, so a
    project that never showed the view saves nothing) and brings the workbench nodes'
    copies up to date through `useWorkbenchTables`'s `bringUpToDate` (a fetch of its own
    awaited, its tables applied at once; a fetch that failed refuses the project's save
    with one error toast, and no pipeline is saved), then saves the pipeline through
    `saveWithPendingCommits`, which waits for those updates to commit. Commit
    (`flushSaveThenMilestone`) opens the milestone modal once that save resolves true.
28. **Synced.** The form store subscribes to `executionGeneration` at module load and runs
    `sync` on each advance, after any save in flight: `fetchWorkbenchForm`, then nothing at
    the same revision, the form adopted (history dropped) while `dirty` is false, else
    `stale` set; a failed read is one error toast and leaves the form as it is.
29. **Preview.** Preview in `WorkbenchToolbar` calls `showSection("preview")`:
    `WorkbenchView` renders `PageTabs` read only over `SheetCanvas`, which gives the
    components `usePreviewValues` (the preview store's `quote`, `setCell` and `setRows`, its
    `price`, `valuesBasis` of the schema and the quote, and `quoteProblems` while `checked`)
    and draws each `SheetWidget` neither editable nor selected, with no ghost;
    `WorkbenchPalette` disables its items, and `useWorkbenchShortcuts` leaves the editing
    keys and undo alone. Build calls `showSection("sheets")`.
30. **Priced on request.** Price calls the preview store's `priceQuote`: nothing while a
    pricing runs, the form is not read or the view has given no pricer; else
    `quoteProblems` on the schema and the quote, and with any the store sets `checked` and
    `error` ("N cells need attention") and stops; else `priceForm({...form, sample: quote},
    pricer)`, the store keeping `{basis, tables, sample}` or "Pricing failed: …" unless a
    Clear or a change of pricer came first, when the answer is dropped. Clear, after
    `window.confirm`, empties the quote, `checked`, the price, the error and the pricing
    flag.
31. **Switched.** After a branch switch in place (`GitPanel`'s lane menu, `BranchManager`)
    has succeeded, `discardFormEdits` (`hooks/useProjectDirty.ts`) reads the form again
    when it held unsaved edits the user chose to discard, so the destination's form shows;
    a switch that failed keeps them, and a form as saved is left to the adoption's sync.

## Edge cases and invariants

- The workbench routes are under `/api/`, so the session-cookie check applies, and they are
  included with the feature routers, before the `/api` 404 guard and the SPA catch-all.
- Nothing is cached: each request reads `haute.toml` and the form from disk, so a change to
  either reaches the next request, and a server never serves a stale form after a branch
  switch.
- A disabled workbench changes nothing in the editor: the store is never set, no fetch is
  made, the palette offers the Quote Input and the Quote Response, and a workbench node the
  pipeline already has keeps its copy.
- `form` resolves inside the project root: `../other/form.json` is refused, as is an absolute
  path and the default `forms/form.json` when `forms` is a symlink or junction to somewhere
  outside the project, so the workbench never reads a file outside it. A path such as
  `forms/../other/form.json`, which resolves to `other/form.json` inside the project, is
  accepted as what it resolves to.
- `index: false` and an empty `sample` are written like every other field: the file has one
  spelling, and a form written by hand without them reads back with their defaults and is
  written back with them.
- Only a Workbench Input takes the workbench's tables: a fetch never changes a Quote Input,
  whose connections still follow a rename by position as `migrateApiInputEdges` allows.
- Each Workbench Input gets each fetch's tables at most once per generation: the fetch-driven
  pass and a palette drop's report never send the same update twice, and an undone update
  stays undone until the next fetch. An update that does not commit, such as one whose node a
  submodel hid while its identity was resolving, is forgotten through `onSettled`, so the next
  pass, when the top level shows again or the next fetch arrives, sends it again.
- A fetch that completes while a submodel is open, or the document is read-only, applies
  when the top level shows again and the document can change.
- An empty `tables` list is a valid answer, and so is one whose tables have no columns yet:
  the Workbench Input has no ports, and `workbench_table_labels` and `workbench_table_frames`
  refuse to make any, each saying what to add.
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
- `haute init --force` without `--workbench` writes a `haute.toml` without the table and
  leaves `forms/` in place, as it leaves `data/`.
- The form's revision is the hash of the file's bytes: a hand edit that changes only
  whitespace is a change, since the view read other bytes, and a rewrite of the same
  canonical text has the same revision.
- The form is read once per store life, so unsaved edits and history survive switching
  views; a reload is explicit (the stale banner's Reload, or Try again after a failed read).
- A save queues behind the one running and writes the form as it stands then, quoting the
  revision the previous save gave, so two quick saves never race on the file; a sync
  queues the same way, so it never reads the file from before a save in flight and takes
  it for another branch's.
- A form save on the ledger is the pipeline save's capture: no working branch, no capture;
  a failed capture is a warning on a 200, the file written; the file is tracked from its
  first captured save, so a milestone's sweep takes a hand edit to it.
- A save saves the form only while it holds unsaved edits or an uncaptured save: a save
  or a milestone from the pipeline editor, the form as saved, makes no form request and
  no form toast. A form save refused, as stale or failed, saves no pipeline and refuses
  the milestone, as a refused pipeline save refuses the milestone: the pipeline never
  carries copies of a form the view does not show. A save of a document that cannot be
  saved, or one asked for inside a submodel, is refused before the form is saved, with
  the pipeline save's reason, so the form is never saved ahead of a pipeline that is not.
- A save saves the form before the pipeline and brings the workbench nodes' copies up to
  date in between, so the pipeline it records carries the form it records; when the
  tables cannot be fetched, no pipeline is saved and no milestone asked for.
- Discard on a switch drops the form's unsaved edits only once the switch has succeeded,
  by reading the file again then; the form store itself never drops a dirty form on a
  document adoption.
- A form save runs under the pipeline save's lock: of two saves quoting one revision, the
  second reads the first's and is refused; the ledger capture never races a pipeline
  save's on the git index.
- The editor's navigation guards (a move, a switch, an archive, a delete) read the form's
  unsaved edits through the workbench store's `formDirty`, mirrored by the form store, so
  a form edited and not saved is asked about as the canvas is.
- An answer to a Preview pricing is published only while it is the pricing asked for last
  and the view's pricer is the one it ran with: Clear and leaving the view drop it.
- Fit clamps through `setZoom`: a sheet too wide to fit at the minimum fits at the minimum.
- A sync adopts a changed file only while the form is as saved, and drops the history, since
  an undo would bring the other branch's form back; with unsaved edits it marks the form
  stale, and the save that would overwrite the file is refused as before.
- The pipeline's document, its live sync and its history go on while the view shows; only
  its keyboard and React Flow's document-level keys are fenced.
- Fields are held by table and column id, never by name, so renaming a column keeps it
  on the sheet; a column or table removed from the schema is dropped from the fields that
  showed it, and a field whose column is gone otherwise is shown as missing and reported.
- The view store is read against the form: a `pageId` the form no longer has reads as the
  first sheet, and a `selectedId` the form no longer has as no selection.
- A press that does not move records nothing; the tracker's listeners are removed on
  release, pointer cancel included.
- The last sheet cannot be removed: `removePage` refuses it and the tabs offer no cross
  for it.
- The sample's values are text or a tick as typed; nothing on the client types them, so
  the posted form always fits `FormSpec` and the server's rules decide what a value is.
- `pricingBasis` is memoised per form object and covers the schema and the sample alone:
  a move, a resize, a rename of a sheet or a component changes nothing in it.
- One pricing runs at a time, and a pricing never writes into the store after the pricer
  it ran with was replaced or cleared.
- The quote is the preview store's alone: never in the form, never saved, never on the
  undo stack; it outlives a trip to Build and to the pipeline editor, and a column removed
  from the schema leaves its values in the quote unread, since `quoteProblems` and the
  server read the schema's columns, not the quote's keys.
- `rowFilled` and `filled` mirror the server's `_record` and `_filled`: a row counts when
  any column but the index holds a tick or text other than blank, so a grid's filled rows
  are the typed rows the pricing answers with, in order, and the index is the grid's row
  number.
- A Table's output rows are matched by every key column's value as the server typed it,
  compared as text; a key missing or null on either side matches nothing. A price for
  other values is shown dimmed on every row, and may line up with the grid differently
  until the next pricing.
- `cellProblem` is stricter than the server in one place: a fraction in a whole-number
  column is a problem here, where the server would keep the float for the pipeline to
  refuse. `parseTypedNumber` reads what the server reads, a decimal with a sign, a point
  or an exponent once the currency sign, separators and spaces are gone, and nothing else:
  the server applies the same rule (`_NUMBER` in `src/haute/_workbench_tables.py`) before
  `float`, so neither reads what Python's `float` would and the other refuses (an
  underscore, a digit outside ASCII).
- `columnNameProblem` holds a column's name to the server's `is_frame_label`, an ASCII
  identifier that is not a Python keyword, so a keyword is refused in the schema editor
  before the tables route refuses it; the Workbench Output's panel judges a table's name
  with the document's reserved labels, as the Workbench Input's does.

## Error handling

- `WorkbenchConfigError` and `WorkbenchFormError` are `WorkbenchError`s; `_error_handlers.py`
  answers any `WorkbenchError` as 409 with its message, logged as a warning. The message names
  the key or the file and says what to set; the path is kept in the error's context for the
  log. `WorkbenchConfigError` is also a `ConfigError`, so a caller that reads `haute.toml` for
  another purpose and catches `ConfigError` catches it too.
- `read_form_document` returns the blank form, with no revision, for a file that does not
  exist, and raises `WorkbenchFormError` for an unreadable one (chaining the `OSError`), one
  that is not UTF-8 text (chaining the `UnicodeDecodeError`), one that is not JSON (chaining
  the `JSONDecodeError`), and one that does not fit `FormSpec` (chaining pydantic's
  `ValidationError`, its first error in the message: enough to find the file's problem,
  since the rest usually follow it). A sample cell is text or a tick, strictly (`Cell`): a
  number or a null does not fit.
- `get_workbench_tables` raises `HTTPException` 404 while the workbench is not enabled, and
  answers a `WorkbenchTablesError` through `contract_error_http_exception`, the structured
  422; the request client retries a 5xx like any idempotent request before the store sees
  the failure. `put_workbench_form` answers an `OSError` from the write as 409 naming the
  file ("<form> could not be written: <reason>"), as the pipeline's write routes answer one.
- `fetchWorkbenchStatus` and `fetchWorkbenchTables` throw `ApiError` or a contract error like
  any typed request; the store turns either into one error toast.
- `_save_form` raises `StaleDocumentRevisionError` (`haute.routes._save_pipeline`) with a
  message of its own; `put_workbench_form` answers it as the pipeline save does, 409 with
  `"<code>: <message>"`, logged as a warning with both revisions. A body that is not a
  form is FastAPI's 422.
- `capture_save_in_ledger` degrades a failed capture to a warning and a missing identity
  to `identity_required`, as for the pipeline save, and `put_workbench_form` answers 200
  with them; nothing in the capture fails the save. `sync` reports a read that failed in
  one error toast and leaves the form as it is.
- A save answered with `git_sha` null and a warning, or with `identity_required`, leaves
  `uncaptured` set; the next `flush` saves the form again, unchanged, so the ledger can
  capture it.
- `bringUpToDate` resolves false when its fetch failed (the store's one toast says why);
  `saveProject` then toasts that the pipeline was not saved and saves nothing.
- `fetchWorkbenchForm` and `saveWorkbenchForm` throw `ApiError` or a contract error like
  any typed request; the form store shows a read failure in the view (`loadError`) and a
  save failure as one error toast, telling a stale refusal by the detail's code.
- `priceQuote` turns a quote that breaks a rule into the preview store's `error`, "N cells
  need attention", and a failed pricing into "Pricing failed: " and the reason; neither is
  a toast, and a press while a pricing runs does nothing.
- `priceSample` rejects with an `Error` naming the reason: no Workbench Output, a table's
  preview error, or the server's reason for a failed preview request
  (`apiErrorMessage`); the pricing store keeps it as `error` and the toolbar shows it.
- The pure operations fail loud on a caller's mistake: `removePage` on the last sheet,
  `updateWidget` giving a Collection `rows` or a Table `columns`, and any operation naming
  a sheet or a component the form does not have, each throw an `Error` naming it.
- An update superseded through `isCurrent` resolves `{ok: false}` with the commit
  controller's message, which it toasts, and changes nothing.
- `validate_singleton_groups` raises `ValueError` naming the group and how many it found;
  save answers it as a 400 and `resolve_config` lets it propagate. The assistant's
  operations raise `OpValidationError` for a Workbench Input they may not author.
- `WorkbenchTablesError` (`src/haute/_workbench_tables.py`, `error_code`
  `workbench_tables_invalid`, one of `PUBLIC_CONTRACT_ERROR_TYPES`) is a workbench node's
  tables the pipeline cannot take: a copy not as the workbench writes it ("The <node>'s
  tables are not as the workbench writes them (<where>: <what>). Open the pipeline in the
  editor and save it.", the spot from pydantic's first error), a name that cannot be a port's
  or a frame column's, or a name used twice, across tables whatever its case. The tables
  route answers it 422 through `contract_error_http_exception`, and a preview's worker
  answers it 422 as it answers any public contract error. `WorkbenchInputError`
  (`src/haute/_workbench_input.py`, `error_code` `workbench_input_invalid`, listed in
  `PUBLIC_CONTRACT_ERROR_TYPES` itself, since a preview's worker reports an error by its
  exact class), its subclass, is a Workbench Input with no tables, or with tables that have
  no columns yet, a sample or a request that does not fit, and a request of other than one
  quote; `api_input_table_labels` turns a `WorkbenchTablesError`
  into `NodeDataPointInvalidError`, an error row in the cache report, and deploy's
  `_workbench_tables` into a `ValueError` naming the node.
- `WorkbenchOutputError`, an `ExecutionError` with no public error code, carries every
  failure of a Workbench Output's run: no tables, a port with no connection, a connection on a
  port that is none of its tables or on a port another connection has, a mapping entry for a
  table or column it does not have, a mapping entry naming a column its frame lacks, a column
  whose dtype does not fit, and a one-row table without exactly one row. It names the table. A
  preview shows it on the node, `run()` and `score()` raise it, and a deployed pipeline answers
  it as its other internal errors, a 500 carrying the message. Tables the pipeline cannot
  take raise `WorkbenchTablesError` from `parse_workbench_tables`, and tables that have no
  columns yet `WorkbenchOutputError`, saying what to add.
- The assistant's operations raise `OpValidationError` for a Workbench Output they may not
  author, as for a Workbench Input.
- `DeployConfig.from_toml` raises `ValueError` for a key under `[workbench]` outside
  `WORKBENCH_TOML_KEYS`, as for any unknown `haute.toml` key.

## Testing

- `tests/test_workbench_config.py` covers the table absent (and `haute.toml` absent), enabled
  and disabled, the default and a configured form path, and each refusal (not a table, an
  unknown key, `enabled` missing or not a boolean, `form` empty, not a string, absolute or
  escaping the project, the default path through a `forms` symlink to somewhere outside the
  project, malformed TOML, a `haute.toml` that is not UTF-8), and that `DeployConfig.from_toml`'s
  whole-file check accepts the table and refuses an unknown key under it.
- `tests/test_workbench_form.py` covers the blank form's text, a saved form round-tripping
  through `write_form` and `read_form_document` with the file's revision (the test project's
  shape: one-row and many-row tables, an index column, Tables and Collections, a sample),
  the same bytes reading as the same revision and a hand edit as another, a missing file as
  the blank form never saved, every field written (a column without `index` reads back with
  it and is written with it, an absent `sample` written as `{}`), an unknown field and an
  out-of-range widget refused, and the message for an unreadable, non-UTF-8, non-JSON and
  misshapen file.
- `tests/test_workbench_tables.py` covers input tables in schema order as the pipeline holds
  them (names, rows per quote and typed columns, the form's keys and rules left behind, the
  frame and quote schemas), each column type's dtype held equal to the Quote Input's, a
  table without columns being no port, every refusal of `parse_workbench_tables` (not a
  list, a key the workbench does not write, a key missing, a type that is not a column's, a
  name that cannot be a port, a name used twice, names differing in case, a column that
  cannot be a frame's, a column named twice), a form's bad table name refused by the node's
  name, the index column, output tables in the same shape, and the sample: typed values,
  currency and separators, ticked and unticked boxes, empty rows left out, a value that no
  longer fits its column kept as typed, the index numbering rows as they stand, and an empty
  sample for a form with nothing typed.
- `tests/test_workbench_routes.py` covers `GET /api/workbench` disabled and enabled, `GET
  /api/workbench/tables` serving the test project's form from the working directory (tables,
  sample and response tables), 404 while disabled, a missing form as the blank form with no
  tables and no revision, 409 with the message for a form that is not UTF-8 (on both the
  tables and the form route), a misshapen form and a bad `[workbench]` table, the structured
  422 for a table named with a keyword, `GET /api/workbench/form` with every field and the
  revision, a save against the revision adopting the new one and reaching the tables, a
  stale save refused with the pipeline's code and the file untouched, a first save creating
  the file unless one appeared, a body that is not a form refused without a write, the form
  routes' 404 while disabled, a save as one commit on the ledger (its SHA, the new file
  alone in it, no second commit for the same form, the milestone folding it in and sweeping
  an edit made by hand), a save without a working branch captured nowhere, a capture that
  failed or waits on an identity reported on a 200 with the file written, a write that fails
  answered 409 naming the file, two saves against one revision taking turns with the second
  refused, and the real
  `haute.server` app answering `GET /api/workbench` ahead of its 404 guard and only with
  the session cookie.
- `tests/test_cli_init.py` covers `haute init --workbench` writing the table and
  `forms/form.json` and reporting both, a plain `haute init` writing neither, and `--force
  --workbench`; `tests/test_scaffold.py` covers `haute_toml`'s table only when asked, parsed,
  and `starter_form` as valid, blank JSON.
- `tests/test_api_contracts.py` holds the three workbench paths, five routes, in the API's
  fingerprint.
- `frontend/e2e/workbench.spec.ts` drives the journey in a browser against the real server:
  the workbench switched on for the project and its sheets written, the scaffold's Quote
  Input and Quote Response replaced through the API by a Workbench Input, a Transform and a
  Workbench Output, the view opened, the schema read, a Collection dragged out of the
  palette onto the sheet and given its fields, the sample typed and priced live, a quote
  priced in Preview apart from the sample, and Save writing the component and the sample
  to `forms/form.json` with its toast.
- `frontend/src/stores/__tests__/useWorkbenchStore.test.ts` covers loading through the
  generated contract (with the form's path), the untouched store while the workbench is not
  enabled, the failure toast (also for a contract violation), `showView` refusing the
  workbench's view while it is not enabled, and `refreshTables`: nothing while not enabled, the
  numbered fetches and the one that publishes, the sample each keeps, the kept tables with
  one toast when the newest fetch fails, and `awaitTables` settling with whether the newest
  fetch published, a superseded fetch taking the newer one's outcome.
- `frontend/src/utils/__tests__/workbenchTables.test.ts` covers the copy rules and what the
  palette's nodes start with; `frontend/src/hooks/__tests__/useWorkbenchTables.test.tsx` when
  tables and samples apply and to what, never a Quote Input or a Quote Response, a retried
  update, a node created while a fetch ran, and `bringUpToDate` bringing a node no render
  reached up to date once its own fetch settles, reporting a fetch that failed, and
  fetching nothing while the document cannot change;
  `frontend/src/hooks/__tests__/useGraphCommitController.pending.test.ts` an update superseded
  through `isCurrent`; `frontend/src/panels/__tests__/NodePalette.test.tsx` the palette's swap
  while the workbench is enabled, with the newest tables and sample; and
  `frontend/src/__tests__/editors/WorkbenchInputEditor.test.tsx` and
  `frontend/src/__tests__/editors/WorkbenchOutputEditor.test.tsx` the panels, their notes
  while the workbench is not enabled and while a submodel is open, and the mapping's picks.
- `frontend/src/__tests__/App.integration.test.tsx` and
  `frontend/src/__tests__/App.backgroundJobsIsolation.test.tsx` stub the status as not enabled;
  the first enables it for the host's case: the switcher at the bottom of the palette, the
  pipeline editor hidden and inert while the view shows, the toolbars swapped, Ctrl/Cmd+S
  saving the form's edits and then the pipeline, the way back, Commit in the workbench's
  toolbar saving the form's edits and then the pipeline with its Workbench Input carrying
  the tables fetched for the saved form before the milestone modal opens, Commit refused
  with no pipeline saved when that fetch fails, Save in the pipeline toolbar saving the
  form's unsaved edits and then the pipeline with the fetched tables, and Save refused
  before the form is saved while the document cannot be saved.
  `frontend/src/__tests__/App.workbenchLazy.test.ts` guards that the view, its toolbar, the
  schema editor, the form store and the form's operations are loaded lazily.
- The view: `frontend/src/stores/__tests__/useWorkbenchFormStore.test.ts` covers reading
  through the contract (once), a read that fails or breaks the contract, edits with undo
  and redo and `dirty`, the `MAX_HISTORY` cap, a save against the revision (the PUT's body,
  the new revision, the toast, the tables fetched again, the next save quoting it), edits
  during a save staying unsaved, a stale refusal keeping the edits until a reload, any
  other failure's toast, no save, edit or snapshot before the form is read, nothing undone
  or redone with nothing to, `forms/form.json` named until the status says where the form
  is, the capture reported (the ledger
  commit, each warning, the identity prompt and the flush that saves again, a failed
  capture leaving the form uncaptured until the next flush), `flush` saving only unsaved
  edits or an uncaptured save and refused with a refused save, `dirty` mirrored onto the
  workbench store for the editor's guards, and `sync` (the same revision, a changed file
  adopted while clean, stale while dirty, no request before the form is read, its turn
  after a save in flight, a failed read's toast);
  `frontend/src/stores/__tests__/useWorkbenchViewStore.test.ts` a sheet shown on the sheets
  section while building and in Preview as before, `activePage`, the zoom within its bounds
  whether set, stepped or fitted (nothing to fit to without a viewport or a form), and the
  panel's width kept; `frontend/src/utils/__tests__/workbenchForm.test.ts` the pure operations and
  `schemaProblems`; `frontend/src/workbench/__tests__/SchemaEditor.test.tsx` the editor
  (tables and types listed, a table added with its name focused, a rename committing as one
  undo step, role and rows with keys dropped, the type marker and the key toggle, Enter
  adding a column and Alt+Down moving one, a column's label and rules, removals with their
  confirmations, the index, problems, collapsing);
  `frontend/src/workbench/__tests__/WorkbenchView.test.tsx` the view's states, the stale
  banner, the shortcuts and the panels beside it;
  `frontend/src/workbench/__tests__/WorkbenchToolbar.test.tsx` its controls, Save and
  Commit through the host and off while editing is disabled;
  `frontend/src/workbench/__tests__/ViewSwitcher.test.tsx` the switcher; and the two
  editors' tests the "Edit in Workbench" button.
- The sheets: `frontend/src/utils/__tests__/sheetGeometry.test.ts` covers the grid, moves
  kept on the sheet, resizes from each handle, the next free spot, the sheet's size and
  the fit; `frontend/src/utils/__tests__/workbenchForm.test.ts` also the sheets (named in
  turn, renamed, the last kept), the components (created with their kind's layout, updated
  with the kind guards, duplicated, removed), the fields (toggled, moved) and
  `widgetProblems`; `frontend/src/hooks/__tests__/useListReorder.test.tsx` the shared
  drag-reorder hook; `frontend/src/workbench/__tests__/SheetCanvas.test.tsx` (on the
  fixtures in `frontend/src/workbench/__tests__/fixtures.ts`) the components drawn where
  the form places them, the problem frame, selection, a drag moving one snapped and
  divided by the zoom as one undo step, a resize by a handle at its minimum, a component
  dragged out of the palette and dropped (the chip, the ghost, a click adding nothing), the
  fit (a very wide sheet at the zoom's minimum), and Preview (nothing selected or moved, no
  drop, the quote typed);
  `frontend/src/workbench/__tests__/PageTabs.test.tsx` the tabs, adding, renaming and
  deleting with the confirmation, and read only in Preview;
  `frontend/src/workbench/__tests__/PropertiesPanel.test.tsx` the title and layout, the
  fields ticked, a table greyed out, the order and the pointer to the schema;
  `frontend/src/workbench/__tests__/useWorkbenchShortcuts.test.tsx` Ctrl/Cmd+S through the
  host with a focused field committed first, the sheet's keys, their guards, the nudge
  burst and Preview's; and
  `frontend/src/workbench/__tests__/WorkbenchPalette.test.tsx` the palette, its collapse
  with the node palette's and its items disabled in Preview.
- The sample: `tests/test_workbench_routes.py` covers `POST /api/workbench/tables` for an
  unsaved form (its tables, sample and response tables, the file untouched), the structured
  422 and a misshapen body, and 404 while disabled; `frontend/src/utils/__tests__/workbenchForm.test.ts`
  `withCell`, `withSampleCell`, `withSampleRows` and `pricingBasis` (a layout change
  changing nothing); `frontend/src/stores/__tests__/useWorkbenchFormStore.test.ts` the
  sample as part of the form, unsaved until saved and undone like any edit;
  `frontend/src/workbench/__tests__/priceSample.test.ts` the top-level nodes patched and
  the Workbench Output previewed per table, the document and its submodels untouched, and
  each refusal; `frontend/src/stores/__tests__/useWorkbenchPricingStore.test.ts` pricing
  as the form stands, once typing pauses, one at a time with one more for the latest and
  the last values dimmed meanwhile, the reason on failure, nothing without a pricer or
  before the form is read, and a late answer dropped;
  `frontend/src/workbench/__tests__/WidgetBody.test.tsx` typing into a Collection's boxes
  and a Table's grid with rows added and deleted, an output box's value and its dimming, a
  Table's output columns matched by key and dimmed once typed over, and a press on a cell
  selecting the component without moving it;
  `frontend/src/workbench/__tests__/WorkbenchView.test.tsx` the pricer set and cleared and
  the pricing scheduled on the basis alone, while building; and
  `frontend/src/workbench/__tests__/WorkbenchToolbar.test.tsx` the failure's reason.
- Preview: `frontend/src/utils/__tests__/sheetValues.test.ts` covers filled rows, numbers
  as the server reads them, each rule, `quoteProblems` by cell, the basis and `pricedRows`;
  `frontend/src/stores/__tests__/useWorkbenchPreviewStore.test.ts` the quote apart from the
  sample (cells, rows, clear), pricing through `priceForm` with the quote in the sample's
  place and the typed sample kept, the check that marks and counts instead of pricing, a
  failure's reason, an answer dropped after Clear or a change of pricer, and nothing
  without a pricer, before the form is read or while a pricing runs;
  `frontend/src/workbench/__tests__/WidgetBody.test.tsx` the quote typed apart from
  the sample with its marked cells and its own price;
  `frontend/src/workbench/__tests__/WorkbenchToolbar.test.tsx` Build and Preview, Price,
  Clear with its confirmation and the quote's reason; and
  `frontend/src/workbench/__tests__/WorkbenchView.test.tsx` Preview read only without the
  properties panel.
- The quote's tables: `tests/test_config_io.py` writes a Workbench Input's config file, its
  sample included, to `config/workbench_input/` and reads it back as ports, and refuses one
  with a `path`.
- The Workbench Input: `tests/test_request_inputs.py` runs the checker over `src/haute` after
  its fixtures show each allowed form passing and each other form reported; holds
  `REQUEST_INPUT_KINDS` and the editor's `REQUEST_INPUT_TYPES` equal to
  `REQUEST_INPUT_NODE_TYPES`; holds a Quote Input's labels, a workbench table's names and the
  editor's source handles to `is_frame_label` alike; shows a Workbench Input beside a Constant
  as the request input a Quote Input with the same tables is through codegen and parsing
  (with no `path` in its config file), data points, `Pipeline.score`, deploy scoring and
  trace lineage, while its preview yields nulls and it has no snapshot; previews one typed
  null row per table, with a join downstream admitted, and refuses tables without columns as
  ports; reads its table points directly, in the cache report and through the node-data
  routes' point, run and clear too; derives its request schema and dry-run quote (a copy not
  as the workbench writes it refused by deploy) and resolves a deploy without a sample file;
  runs its generated code to the same null rows and parses a submodel's `workbench_input`;
  previews, runs and leases its sample's rows, a downstream calculation included, with no
  rows for a many-row table the sample leaves out, a null for a column a row leaves out and
  the null quote while there is no sample (`test_a_workbench_input_previews_its_sample`);
  reads a sample as the request it stands for, frame for frame
  (`test_a_sample_prices_as_its_quote_would_when_deployed`, and at the form's level in
  `tests/test_workbench_tables.py`); cuts the sample to a demand's ports and columns; reads each value as its column's type and refuses each that is not, naming it;
  changes a table point's version with the sample; fails each kind of misfit wherever rows
  are read, a join's planning and the preview route's worker (422) included, while the cache
  report still has the points; never reads the sample for a request
  (`test_a_request_never_reads_the_sample`); reads a deployed request into the tables, one
  quote per request, from a frame or from the records of a `QuoteRequest`, a quote leaving a
  table out scored, each misfit refused naming its spot and a `QuoteRequest` refused for a
  Quote Input (`test_a_deployed_pipeline_reads_the_request_into_the_tables`); sizes each
  port's rows for the memory estimate
  (`test_a_deployed_request_is_sized_per_table_for_the_memory_estimate`, with the index in
  `tests/test_ram_estimate.py`); answers a misfit through the generated container's `/quote`
  as the structured 422 (`test_the_containers_quote_route_reads_the_request_as_sent`); and
  refuses a second request input in save and in `resolve_config`.
  `tests/test_deploy_batch_scoring.py` keeps a null sample's types in the hard-capped worker,
  for a one-row and a many-row table; `tests/test_deploy_internals.py` builds the nested
  MLflow signature and refuses a `Decimal` inside a table;
  `tests/test_deploy_expected_output_validation.py` walks an expected output into a Workbench
  Output's tables with the tolerance and lets a quote leave a table out of the pre-check. `tests/test_assistant_ops.py` covers the
  assistant's refusals and its wiring to a Workbench Input's frames, and
  `tests/test_codegen_roundtrip_property.py` round-trips one in its corpus.
- In the frontend, `frontend/src/utils/__tests__/requestInputTypes.test.ts` runs the editor's
  guard over `frontend/src` after its fixtures, and covers `isRequestInputType` and the shared
  singleton slot; `frontend/src/utils/__tests__/apiInputPorts.test.ts` a Workbench Input's
  frames (its tables with a column, named by their tables, judged as labels are) and their
  columns, and connections following names; `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts` a palette
  drop reporting its node; and `frontend/src/hooks/__tests__/useNodeDataCache.test.tsx` a point
  that reads directly clearing nothing.
- The Workbench Output: `tests/test_workbench_output.py` covers the tables read from a config
  (no tables refused, tables without columns refused), the
  mapping (picks, none, an entry for a table or column the node lacks refused), filling (by
  name, by pick, a pick the frame lacks, a dtype that does not fit, a typed null for a column
  nothing fills, a one-row table with other than one row, the columns cut to the table's in
  order), the response document and `as_response` for either response node, a pipeline running
  and scoring to it, codegen and parsing of `target_port`, the second response node refused in
  save and deploy, deploy pruning to it and serving its response, and the preview's table
  picker. In the frontend, `frontend/src/utils/__tests__/workbenchTables.test.ts` covers a
  copy read and its ports, `frontend/src/nodes/__tests__/PipelineNode.test.tsx` its
  port rows, `frontend/src/utils/__tests__/connectionValidation.test.ts` the connection rules,
  `frontend/src/utils/__tests__/nodeUpdatePlan.test.ts` the connections a tables update
  removes, and `frontend/src/hooks/__tests__/usePipelineAPI.nodeDataEpoch.test.ts` the preview's
  first table.
