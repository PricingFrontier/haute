# Workbench — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_workbench_config.py` | The `[workbench]` table of `haute.toml`: `WORKBENCH_TOML_KEYS` (`enabled`, `form`), `DEFAULT_FORM_PATH` (`forms/form.json`), `WorkbenchError` (the base of every workbench problem the analyst fixes in the project), `WorkbenchConfigError`, the frozen `WorkbenchConfig` record (`enabled`, `form` as `haute.toml` names it, `project_root`, and `form_path`) and `read_workbench_config` (the table read and checked from the project root's `haute.toml`: disabled without the file or the table). |
| `src/haute/_workbench_form.py` | The form file's canonical shape and its reading: the pydantic models `FormSpec`, `Schema`, `SchemaTable`, `SchemaColumn`, `Page`, `TableInputWidget`, `CollectionWidget` and `FieldRef` (every field written, unknown fields refused), `WorkbenchFormError`, `blank_form` (one blank sheet, named after the project), `render_form` (the file's text), `write_form` (an atomic write) and `read_form` (the file read whole and checked, naming the file and what is wrong). |
| `src/haute/_workbench_tables.py` | The form's tables as Haute takes them: `input_tables` (the schema's input tables in the Quote Input's v2 shape, in schema order), `output_tables` (its output tables in the same shape), `sample_quote` (the sample typed while building as one request holds it, each value as its column's type holds it) and `workbench_tables`, which gathers the three as a `WorkbenchTables`. |
| `src/haute/_workbench_output.py` | The Workbench Output's tables and response: `WorkbenchOutputError`, `workbench_output_tables` (its tables read from its config: checked with `validate_v2_schema`, each one-row or many-row by its path, each column at its table's level), `workbench_output_mapping` (its mapping read from its config, checked against the tables), `WorkbenchOutputTables` (its result: its tables' frames by label, carrying the tables they fill), `fill_workbench_tables` (the tables filled from the frames by port through the mapping, each column checked against and cast to its declared type, a one-row table checked for its one row when it is read), `workbench_response` (the response built from the tables by the Quote Response's assembler) and `as_response` (a response node's result as the frame a request is answered with). |
| `src/haute/routes/workbench.py` | `router`, the workbench routes under `/api/workbench`: `workbench_status` (`GET /api/workbench`) and `get_workbench_tables` (`GET /api/workbench/tables`), each reading `haute.toml`, and the second the form, from the working directory on every request, off the event loop, the tables and the response tables checked with `validate_v2_schema`. |
| `frontend/src/api/workbench.ts` | `fetchWorkbenchStatus`: `GET /api/workbench` through the shared request machinery, validated by the generated `workbench` contract, whose validators load with the first response. `fetchWorkbenchTables`: `GET /api/workbench/tables`, validated by the same group's `WorkbenchTablesResponse`. |
| `frontend/src/stores/useWorkbenchStore.ts` | `useWorkbenchStore`: `enabled`, whether the project's workbench is; `load`, which fetches the status, leaves the store untouched while the workbench is not enabled (so the editor never re-renders for it) and reports a failure as one error toast; `tables`, the workbench's tables, sample and response tables from the newest fetch that succeeded; and `refreshTables`, which numbers its fetches so that only the newest publishes them or toasts its failure. |
| `frontend/src/utils/workbenchTables.ts` | The copy rules for a Workbench Input and a Workbench Output: `WorkbenchTables`, `quoteTablesPatch` (the update a Workbench Input's copy needs, `{tables, sample}`, both compared as JSON with object keys sorted and a missing sample read as `{}`, or null), `responseTablesPatch` (the update a Workbench Output's copy needs, `{tables}` compared the same way and `mapping` without the entries of tables and columns the new tables lack, or null), `paletteWorkbenchInputConfig` and `paletteWorkbenchOutputConfig` (what the palette's Workbench Input and Workbench Output start with: the newest tables, and sample, fetched), `workbenchOutputTableLabels` (a Workbench Output's ports, its tables' labels in order), and `WORKBENCH_COPY_PATCHES` (the update rule for each workbench node type, `quoteTablesPatch` for a Workbench Input and `responseTablesPatch` for a Workbench Output). |
| `frontend/src/hooks/useWorkbenchTables.ts` | The hook the editor shell (`frontend/src/App.tsx`) calls to keep those copies current: a fetch when the workbench is enabled and on each `executionGeneration`, recorded against the generation; eligible tables applied once per Workbench Input and Workbench Output for each fetch, by `WORKBENCH_COPY_PATCHES`, through `onUpdateNode`, with an `isCurrent` that checks the generation, while the document is editable and its top level shows; `nodeCreated` for a node a palette drop created. |
| `frontend/src/panels/editors/WorkbenchInputEditor.tsx` | The Workbench Input's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Input: the tables read-only with each label's `apiInputLabelIssue`, a note on what previews run on, chosen by whether the config's sample is a non-empty object, a note while the workbench is not enabled, and one while a submodel is open (`insideSubmodel`, passed down from `frontend/src/panels/NodePanel.tsx`). It exports `WorkbenchTable`, one table read-only, and `WorkbenchTablesHeader`, the section's title and its notes, which the Workbench Output's panel shares, and `WORKBENCH_DISABLED_NOTE`, the note's one wording. |
| `frontend/src/panels/editors/WorkbenchOutputEditor.tsx` | The Workbench Output's panel, which `frontend/src/panels/NodeConfigEditor.tsx` renders for a Workbench Output: under `WorkbenchTablesHeader`, each table with its label's `apiInputLabelIssue`, the node connected to its port (from the `targetHandle` of the panel's input sources) or "Not connected", and a row per column with a select of the connected frame's columns (the input source's `columns`) that fills it, chosen by `mappedSource`, through `onUpdate` with the new `mapping`. |

`src/haute/schemas.py` defines `WorkbenchStatusResponse` and `WorkbenchTablesResponse`;
`scripts/generate_api_contracts.py` lists the two responses as the `workbench` response group;
`frontend/src/api/types.ts` re-exports the generated types. `src/haute/routes/_error_handlers.py`
answers a `WorkbenchError` as 409. `src/haute/deploy/_config.py` lists the `[workbench]` table,
with `WORKBENCH_TOML_KEYS`, in the `haute.toml` schema its whole-file check accepts.
`src/haute/_scaffold.py` renders the table in `haute_toml` when asked and the blank form as
`starter_form`; `src/haute/cli/_init_cmd.py` writes both for `haute init --workbench`;
`src/haute/_git_setup.py` seeds `forms/` into an unborn repository's root commit.

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
Quote Input's place while the workbench is enabled and drags it with
`paletteWorkbenchInputConfig`; `applyApiInputConfigChange` in
`frontend/src/utils/apiInputPorts.ts` takes `followNames`, which
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
- **`WorkbenchTables`** (`src/haute/_workbench_tables.py`): `tables`, `sample` and
  `response_tables`, as `GET /api/workbench/tables` serves them. A table is
  `{path, label, emit, row_id_column, columns}` and a column
  `{name, path, type, status, selected, levels}`, the v2 shape `validate_v2_schema` checks.
- **`WorkbenchStatusResponse`** (`src/haute/schemas.py`): `enabled` and `form` (the path as
  `haute.toml` names it while enabled, else `null`). **`WorkbenchTablesResponse`**: `tables`,
  `sample` (an object, `{}` for none) and `response_tables` (`[]` for none).
- **Store state** (`frontend/src/stores/useWorkbenchStore.ts`): `enabled` (false until a
  status says otherwise) and `tables` (`WorkbenchTables | null`: `tables`, `sample`,
  `responseTables` and `fetch`, the fetch's number).
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

## Control flow

1. **Status.** The editor shell calls `useWorkbenchStore.load()` once. `GET /api/workbench`
   reads `read_workbench_config(Path.cwd())` in the thread pool and answers `enabled` and
   `form`. An enabled status is stored; a disabled one changes nothing; a failure shows an
   error toast naming the cause and the store stays as it started.
2. **The tables, served.** `GET /api/workbench/tables` reads the config the same way, answers
   404 while it is disabled, reads the form with `read_form` in the thread pool, builds
   `workbench_tables(spec)`, checks the tables and the response tables with
   `validate_v2_schema({"tables": ...})`, answering the structured 422 from
   `api_input_schema_error_response` when either fails, and answers
   `WorkbenchTablesResponse` with the tables, the sample (unchecked) and the response tables.
   `input_tables` and `output_tables` make a table per schema table of the role, in schema
   order: a one-row table at `$[:]` with its columns at `$[:].<table>.<column>`, a many-row
   table at `$[:].<table>[:]` with its columns below it, every table emitting, every column
   selected and `Confirmed`, a column's `options` as its `levels`, the index column an `int`
   with none, and a many-row table's single key column as its `row_id_column`. `sample_quote`
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
   `applyApiInputConfigChange` with `followNames` for a Workbench Input, which keeps only the
   connections whose labels remain and reports the rest, and for a Workbench Output keeps each
   connection whose `targetHandle` is one of the new tables' labels and removes the rest, which
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
   Input, `infer_input_schema` in `src/haute/deploy/_schema.py` returns `request_record_schema`
   of its tables as dtype strings, through `_workbench_request_schema`, and `_read_sample_row`,
   which execution-policy planning and the output-schema dry run share, returns one record of
   nulls in that schema. `request_record_schema` walks each emitting table's path, then each
   selected column's, hop by hop as `parse_table_path` and `_parse_dollar_path` split them: an
   object hop is a `pl.Struct` field, an array hop a `pl.List` of `pl.Struct`, and the leaf has
   its declared type; a column at the reserved `$value` leaf makes its array a `pl.List` of
   that type, and a table whose selected columns all sit at an ancestor level keeps its own
   array, as a `pl.List` of an empty `pl.Struct`. When the dry run falls back to the
   hard-capped batch worker, `_capped_worker_output_schema` passes the sample's schema to
   `prepare_batch_scoring` as `input_schema`, carried on `BatchScoreRequest`, and the worker
   builds its input frame with it, so a record of nulls keeps its types; a served request
   passes none.
10. **Connected.** `PipelineNode` renders a Workbench Output's body as `FramePortRows` with
    `direction="target"`, one row per label from `workbenchOutputTableLabels`, each a target
    `Handle` whose id is the label, or "No tables" with none; it has no default input and no
    source handle, and the labels join the signature that re-measures the node's handles.
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
12. **Its tables, previewed.** The walk keeps the result as a multi-frame bundle, as it keeps
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
13. **The response node.** `Pipeline._resolve_output_node` returns the one node whose
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
  spelling, and a form the standalone builder writes without them reads back with their
  defaults and is written back with them.
- Only a Workbench Input takes the workbench's tables: a fetch never changes a Quote Input,
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
- `haute init --force` without `--workbench` writes a `haute.toml` without the table and
  leaves `forms/` in place, as it leaves `data/`.

## Error handling

- `WorkbenchConfigError` and `WorkbenchFormError` are `WorkbenchError`s; `_error_handlers.py`
  answers any `WorkbenchError` as 409 with its message, logged as a warning. The message names
  the key or the file and says what to set; the path is kept in the error's context for the
  log. `WorkbenchConfigError` is also a `ConfigError`, so a caller that reads `haute.toml` for
  another purpose and catches `ConfigError` catches it too.
- `read_form` raises `WorkbenchFormError` for a missing file ("<form> does not exist: create
  it, point [workbench].form at the form, or set [workbench] enabled = false in haute.toml."),
  an unreadable one (chaining the `OSError`), one that is not UTF-8 text (chaining the
  `UnicodeDecodeError`), one that is not JSON (chaining the `JSONDecodeError`), and one that
  does not fit `FormSpec` (chaining pydantic's `ValidationError`, its first error in the
  message: enough to find the file's problem, since the rest usually follow it).
- `get_workbench_tables` raises `HTTPException` 404 while the workbench is not enabled, and
  answers schema errors through `api_input_schema_error_response`; the request client retries
  a 5xx like any idempotent request before the store sees the failure.
- `fetchWorkbenchStatus` and `fetchWorkbenchTables` throw `ApiError` or a contract error like
  any typed request; the store turns either into one error toast.
- An update superseded through `isCurrent` resolves `{ok: false}` with the commit
  controller's message, which it toasts, and changes nothing.
- `validate_singleton_groups` raises `ValueError` naming the group and how many it found;
  save answers it as a 400 and `resolve_config` lets it propagate. The assistant's
  operations raise `OpValidationError` for a Workbench Input they may not author.
- `workbench_table_labels` and `workbench_table_frames` raise `RuntimeError` "This Workbench
  Input has no tables: add input tables to the workbench's schema and save it." when no table
  emits, and `workbench_table_frames` raises `ApiInputSchemaError` for a sample that does not
  fit, naming what does not; `api_input_table_labels` turns it into
  `NodeDataPointInvalidError`, an error row in the cache report. `request_record_schema`
  raises `ApiInputSchemaError` when two paths disagree about a request field, which
  `_workbench_request_schema` turns into a `ValueError` naming the node, as it does for tables
  that give no schema.
- `WorkbenchOutputError`, an `ExecutionError` with no public error code, carries every
  failure of a Workbench Output's run: no tables, a port with no connection, a connection on a
  port that is none of its tables or on a port another connection has, a mapping entry for a
  table or column it does not have, a mapping entry naming a column its frame lacks, a column
  whose dtype does not fit, and a one-row table without exactly one row. It names the table. A
  preview shows it on the node, `run()` and `score()` raise it, and a deployed pipeline answers
  it as its other internal errors, a 500 carrying the message. Tables that break the v2 rules
  raise `ApiInputSchemaError` from `validate_v2_schema`, and a table whose path or a column's
  path is not of the shapes above raises `WorkbenchOutputError`.
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
  through `read_form` and `write_form` (the test project's shape: one-row and many-row tables,
  an index column, Tables and Collections, a sample), every field written (a column without
  `index` reads back with it and is written with it, an absent `sample` written as `{}`), an
  unknown field and an out-of-range widget refused, and `read_form`'s message for a missing,
  unreadable, non-UTF-8, non-JSON and misshapen file.
- `tests/test_workbench_tables.py` covers input tables in schema order with their paths, levels
  and row id, a many-row table without exactly one key having none, the index column, output
  tables in the same shape, and the sample: typed values, currency and separators, ticked and
  unticked boxes, empty rows left out, a value that no longer fits its column kept as typed,
  the index numbering rows as they stand, and an empty sample for a form with nothing typed.
- `tests/test_workbench_routes.py` covers `GET /api/workbench` disabled and enabled, `GET
  /api/workbench/tables` serving the test project's form from the working directory (tables,
  sample and response tables), 404 while disabled, 409 with the message for a missing form, a
  form that is not UTF-8, a misshapen form and a bad `[workbench]` table, the structured 422
  for a table named with a keyword, and the real `haute.server` app answering
  `GET /api/workbench` ahead of its 404 guard and only with the session cookie.
- `tests/test_cli_init.py` covers `haute init --workbench` writing the table and
  `forms/form.json` and reporting both, a plain `haute init` writing neither, and `--force
  --workbench`; `tests/test_scaffold.py` covers `haute_toml`'s table only when asked, parsed,
  and `starter_form` as valid, blank JSON.
- `tests/test_api_contracts.py` holds the two workbench routes in the API's fingerprint.
- `frontend/src/stores/__tests__/useWorkbenchStore.test.ts` covers loading through the
  generated contract, the untouched store while the workbench is not enabled, the failure
  toast (also for a contract violation), and `refreshTables`: nothing while not enabled, the
  numbered fetches and the one that publishes, the sample each keeps, and the kept tables with
  one toast when the newest fetch fails.
- `frontend/src/utils/__tests__/workbenchTables.test.ts` covers the copy rules and what the
  palette's nodes start with; `frontend/src/hooks/__tests__/useWorkbenchTables.test.tsx` when
  tables and samples apply and to what, never a Quote Input or a Quote Response, a retried
  update, and a node created while a fetch ran;
  `frontend/src/hooks/__tests__/useGraphCommitController.pending.test.ts` an update superseded
  through `isCurrent`; `frontend/src/panels/__tests__/NodePalette.test.tsx` the palette's swap
  while the workbench is enabled, with the newest tables and sample; and
  `frontend/src/__tests__/editors/WorkbenchInputEditor.test.tsx` and
  `frontend/src/__tests__/editors/WorkbenchOutputEditor.test.tsx` the panels, their notes
  while the workbench is not enabled and while a submodel is open, and the mapping's picks.
- `frontend/src/__tests__/App.integration.test.tsx` and
  `frontend/src/__tests__/App.backgroundJobsIsolation.test.tsx` stub the status as not enabled.
- The quote's tables: `tests/test_api_input_table_snapshots.py` shreds a two-quote request
  through tables in the workbench's shape into one frame per table; `tests/test_config_io.py`
  writes a Workbench Input's config file, its sample included, to `config/workbench_input/`
  and reads it back, and refuses one with a `path`.
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
  singleton slot; `frontend/src/utils/__tests__/apiInputPorts.test.ts` connections following
  names with `followNames`; `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts` a palette
  drop reporting its node; and `frontend/src/hooks/__tests__/useNodeDataCache.test.tsx` a point
  that reads directly clearing nothing.
- The Workbench Output: `tests/test_workbench_output.py` covers the tables read from a config
  (one-row and many-row by path, a column outside its table refused, no tables refused), the
  mapping (picks, none, an entry for a table or column the node lacks refused), filling (by
  name, by pick, a pick the frame lacks, a dtype that does not fit, a typed null for a column
  nothing fills, a one-row table with other than one row, the columns cut to the table's in
  order), the response document and `as_response` for either response node, a pipeline running
  and scoring to it, codegen and parsing of `target_port`, the second response node refused in
  save and deploy, deploy pruning to it and serving its response, and the preview's table
  picker. In the frontend, `frontend/src/nodes/__tests__/PipelineNode.test.tsx` covers its
  port rows, `frontend/src/utils/__tests__/connectionValidation.test.ts` the connection rules,
  `frontend/src/utils/__tests__/nodeUpdatePlan.test.ts` the connections a tables update
  removes, and `frontend/src/hooks/__tests__/usePipelineAPI.nodeDataEpoch.test.ts` the preview's
  first table.
