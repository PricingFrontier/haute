# Workbench — High-Level Specification

## Purpose

The workbench is where a pricing team lays out the quote an underwriter keys in: a schema
of typed tables, the sheets of Tables and Collections that show those tables' columns, and
the sample quote typed into them while building. It is part of Haute, switched on per
project in `haute.toml`, and its form is a file in the project, `forms/form.json`, versioned
beside the pipeline.

The workbench's schema is where the pipeline's quote tables come from. The pipeline reads
them through a Workbench Input, a node type of its own beside the Quote Input, and keeps its
own copy of them, with the sample quote its previews run on. The schema's output tables are
what a priced quote fills in: the pipeline fills them through a Workbench Output, a node
type of its own beside the Quote Response, whose input ports are those tables.

This component owns the switch, the form file with its reading, its saving and its
revision, the routes that serve the form and its tables to the editor, the
`haute init --workbench` scaffold, the Workbench view in which the form is edited inside
Haute (its host beside the pipeline editor, its toolbar and shortcuts, its schema editor
and its sheets), and the two workbench node types: where their tables come from, how the
editor keeps them current, what a Workbench Input gives without a request and how a
Workbench Output's frames fill the response. The sample typed while building, Preview and
the form's place on the save ledger are planned
([the workbench roadmap](../roadmap/workbench.md)); until they land, a sample is typed into
the form outside Haute, by Obverse's standalone builder, which writes the same file, or by
hand.

## Scope

In scope:

- The `[workbench]` table of `haute.toml`: whether the project's workbench is enabled and
  where its form is.
- The form file: its canonical shape, how it is read and written, and the blank form
  `haute init --workbench` scaffolds.
- The workbench's tables as Haute takes them: the schema's input tables in the Quote
  Input's v2 shape, the sample quote as a request holds it, and the output tables in the
  same shape.
- `GET /api/workbench`, the status the editor reads, and `GET /api/workbench/tables`, the
  tables, sample and response tables as the form defines them now.
- `GET /api/workbench/form`, the form with its file's revision, and
  `PUT /api/workbench/form`, which writes the form unless the file changed since it was read.
- The Workbench view: how it is shown beside the pipeline editor and what the pipeline
  editor does meanwhile, its toolbar and keyboard shortcuts, its schema editor, its sheets
  (the canvas, the Table and the Collection, their palette and properties panel, and the
  sheet tabs), and how the form is read into it and saved from it.
- The Workbench Input, the node type that holds a copy of the tables and sample, which the
  editor keeps current and shows, and previews on.
- The Workbench Output, the node type that holds a copy of the output tables, which the
  editor keeps current and shows, and whose ports take the frames that fill them.

Out of scope:

- The sample typed while building and priced live, Preview, and the form on the save
  ledger. Each is a package of the [workbench roadmap](../roadmap/workbench.md).
- The toolbar's pipeline controls ([frontend-shared](../frontend-shared/high-level.md)) and
  the canvas, palette and keyboard shortcuts
  ([frontend-graph-canvas](../frontend-graph-canvas/high-level.md)).
- The Host, Origin and session-cookie checks
  ([sandbox-security](../sandbox-security/high-level.md)), which apply to the workbench
  routes exactly as to any other `/api/` route.
- How a deployed pipeline reads a request into its tables ([deploy](../deploy/high-level.md)).
- `haute init`'s other scaffolding ([cli](../cli/high-level.md),
  [pipeline-config](../pipeline-config/high-level.md)).

## Behaviour

- **Enabling.** A project's workbench is switched on in `haute.toml`:

  ```toml
  [workbench]
  enabled = true
  form = "forms/form.json"   # optional; this is the default
  ```

  Without the table the project has no workbench and the editor looks exactly as it does
  without this component. With the table, `enabled` is required and `true` or `false`; `form`
  is optional, a path relative to the project root that stays inside it, `forms/form.json`
  when absent; any other key is refused by name. `haute deploy`'s whole-file check of
  `haute.toml` accepts the table with those two keys. `haute init --workbench` writes the
  table and a blank form; `haute init --force` rewrites `haute.toml`, so the table stays only
  when `--workbench` is given again, while `forms/` is left as `data/` is. An existing project
  enables its workbench by adding the table by hand.
- **The form file.** `forms/form.json` is one JSON document: `version` (1), `name`, `schema`
  (its tables), `pages` (its sheets, at least one) and `sample` (the quote typed while
  building, `{}` for none). Every field has one canonical spelling and an unknown field is
  refused. A schema table has an id, a name, a `role` (`input`, sent to the pricing engine,
  or `output`, returned by it), `rows` (`one` per quote, such as policy details, or `many`,
  such as an equipment schedule) and its columns, each with an id, a name, a `type` of the
  Quote Input's column types (`str`, `int`, `float`, `bool`, `date`), a `label`, whether it
  is a `key` (a many-row table's keys say which row is which), its rules (`required`, `min`,
  `max`, `options`), and whether it is the table's `index`, the column numbering its rows
  from 1. A sheet has an id, a title and its widgets: a Table (`tableInput`, a grid of at
  least `rows` rows showing columns of many-row tables) or a Collection (`collection`, boxes
  showing columns of one-row tables, `columns` across), each placed by its `x`, `y`, `w`, `h`
  and showing its `fields`, schema columns named by table id and column id so a rename keeps
  them. The sample holds, by table id, rows keyed by column id, each value as typed: text or
  a tick. Haute reads the file whole whenever it needs it and never keeps it in memory, so an
  edit made outside the view reaches the next request. A file that does not exist is the
  blank form, never saved: one sheet, no tables, no sample.
- **Status.** `GET /api/workbench` answers `enabled` and, while enabled, `form`, the path as
  `haute.toml` names it. The editor reads it once when it starts. While the workbench is not
  enabled nothing in the editor changes on its account.
- **The tables.** `GET /api/workbench/tables` answers the schema's input tables as `tables`,
  the sample quote as `sample` and the output tables as `response_tables`, as the form
  defines them now. It answers 404 while the workbench is not enabled; the tables and the
  response tables are checked by the Quote Input's schema rules
  ([json-shredding](../json-shredding/high-level.md)) and a breach answers the structured 422
  that Infer Tables answers; the sample is served unchecked, since a sample that does not fit
  fails the previews that read it and never stops the tables updating.
- **The form, served and saved.** `GET /api/workbench/form` answers the form as its file
  holds it now and the file's `revision`, the content hash of its bytes: `null` while the
  file does not exist, when the form is the blank one. `PUT /api/workbench/form` takes the
  whole form and the `base_revision` it was read at, writes the file (every field, in its
  canonical spelling) and answers the form with its new revision. A file whose revision is
  no longer `base_revision`, edited by hand or by a branch switch, is left as it is and the
  save is refused with 409 and a `stale_document_revision` detail, as the pipeline's save
  refuses a stale document; a first save (`base_revision` `null`) creates the file unless
  one has appeared. Both routes answer 404 while the workbench is not enabled.
- **The view.** While the workbench is enabled, a view switcher at the bottom of the left
  palette offers "Pricing", the pipeline editor, and "Workbench". The Workbench view
  covers the area below the toolbar: its own left column, the component palette with the
  switcher back, and the section the toolbar chooses, Sheets or Schema. Meanwhile the
  pipeline editor stays
  mounted (live sync, the document and its undo history go on) but invisible and inert, its
  keyboard shortcuts, Ctrl/Cmd+Enter and React Flow's delete and pan keys off and its
  floating menus closed, so a key pressed in the view never edits the hidden pipeline. The
  toolbar keeps the brand and the project's controls (Assistant, Help, the branch and Save)
  and shows the view's own: Sheets over Schema, the form's Undo and Redo, and Zoom In over
  Zoom Out while the sheets show. Save and Ctrl/Cmd+S save the form,
  with a focused field's edit included; the view has no Commit until the form is on the
  save ledger, and the Git panel's own Commit still records the pipeline. The Git and
  Assistant panels open beside the view, in the properties panel's place. The Workbench
  Input's and Workbench Output's
  panels offer "Edit in Workbench", which shows the view. The view reads the form when it
  first shows and keeps it, with its unsaved edits and history, across a trip to the
  pipeline editor; a form that cannot be read is reported in the view, with what is wrong
  and a way to try again.
- **The schema editor.** The form's tables, each with its name, its role (Input, sent to
  the pricing engine, or Output, returned by it), its rows (One row per quote, or Many
  rows) and its columns; each column with its type, drawn as the step editor's marker for
  the kind (a number, text, true/false, a date) in the data preview's colour and changed
  from it, its name and, in a many-row table, whether it is a key. A column's label and, in
  an input table, its rules (required, a range for a number, the allowed values) open
  beside it. Every text field commits on blur or Enter, so an edit is one undo step; Enter
  in a column's name adds the next column, Alt+Up and Alt+Down move one, and a new table's
  or column's name takes the focus. A many-row table can have an index: a column first in
  the table, `row_number` until renamed, an Integer whose type cannot change, numbering
  the rows from 1; switching a table to one row clears its keys. Removing a column or a
  table a sheet shows asks first, and takes it off the sheet and out of the sample. The
  editor says what would stop the schema being the pipeline's tables: a table's name is
  held to the port rules the Quote Input's labels are held to (an identifier, none of the
  document's reserved labels, unique whatever its case), a column's name is an identifier
  unique in its table, a many-row table needs a key, and an input column's range must not
  be inverted and its allowed values must be of its type.
- **The sheets.** A sheet is a canvas on an 8px snap grid that fills its viewport across
  at the zoom and, like a spreadsheet, has no right or bottom edge: it widens and
  lengthens to hold what is on it. Its tabs run along the top: the sheet showing is
  marked, a plus adds a sheet named in turn, double-click renames one, and the showing
  sheet's cross deletes it, asking first when components are on it, while another sheet
  remains; the schema and the sample are untouched. A component is drawn where the form
  places it: a Table as a grid of its rows with a column per field it shows, the index
  column numbering the rows; a Collection as boxes with their labels above, its columns
  across. Each field shows its label (its column's, or the name made readable), a star for
  a required input, a hint at its type (a dropdown's chevron, a date's calendar, a
  number's 0), an output column shaded, and "Missing column" when its column is gone.
  Dragging a component moves it; selected, it has eight handles that resize it, no
  smaller than its kind's minimum. Positions snap to the grid, pointer movement is
  divided by the zoom, and a move or a resize is one undo step, a click that does not
  move recording nothing. Pressing the empty sheet deselects. A component with a problem
  (no fields, a column no longer in the schema, a table of the other kind, tables whose
  rows do not line up) gets a dashed frame in the warning colour naming the problem. The
  zoom runs from 25% to 200% in steps of 10%; what is on the sheet is fitted to the
  viewport, never past 100%, when the sheets first show and on Ctrl/Cmd+1.
- **The palette.** The view's left column is Haute's palette shell with a Table and a
  Collection, in the entry colour, and the switcher under them. A component is dragged
  out of it onto the sheet: a chip follows the pointer until the sheet shows a ghost where
  the component would land; released there, it is added, 720 wide (a Table 200 high with
  3 rows, a Collection 120 high with 3 columns across), showing nothing yet, and selected.
  A click adds nothing. The palette shares the node palette's open state, so collapsing it
  in either view collapses both; collapsed, it is the reveal strip with the compact
  switcher under it.
- **The properties panel.** Selecting a component opens its panel on the right, Haute's
  side panel resized from its left edge, where the Git and Assistant panels open while
  they are closed: the component's kind and its title; its layout, a Table's rows (1 to
  50) or a Collection's columns across (1 to 12), a value outside the range refused at the
  field; the order of the fields it shows, moved by dragging or Alt+Up and Alt+Down, each
  with a cross that stops showing it; and its fields, ticked from the schema tables of its
  kind, many-row tables for a Table and one-row tables for a Collection, each table
  collapsible with its role and how many of its columns show, each column with its type's
  icon, its name and its label. Fields are held by table and column id, in the order
  ticked, so a rename keeps them. A grid holds one kind of row, so once a Table shows a
  field, tables whose rows do not line up with it (keyed by other columns, or keyless) are
  greyed out with their unticked columns disabled; an input table and an output table
  keyed alike line up. With no table of the component's kind in the schema, the panel
  says so and offers the schema.
- **The sheet's keyboard.** While the sheets show and a component is selected: Escape
  deselects it, Delete or Backspace removes it, Ctrl/Cmd+D duplicates it (16px below and
  to the right, selected), and the arrow keys nudge it by a grid step, or a pixel with
  Shift, a burst of nudges within 800ms being one undo step. None of that from a text
  field or a select, whose own keys pick an option.
- **Saved.** A successful save adopts the file's new revision and fetches the tables
  again, so the Workbench Input's and Workbench Output's copies follow the schema and the
  pipeline has changes to save, as after any fetch. A save refused as stale keeps the edits
  and says so, in a toast and in a banner in the view whose Reload reads the file again,
  dropping the edits and the history.
- **The tables as Haute takes them.** The input tables are the Workbench Input's, in schema
  order. In each quote a one-row table is an object under its own name (table path `$[:]`,
  columns `$[:].<table>.<column>`) and a many-row table is an array of objects under its name
  (path `$[:].<table>[:]`, columns `$[:].<table>[:].<column>`); a request is one quote or a
  list of them. Column types carry over, a column's allowed values become its `levels`, the
  index column is an Integer with no levels, every column is selected, every table emits, and
  a many-row table with exactly one key column uses it as its `row_id_column`. Rules such as
  required and ranges stay in the form. The output tables are the Workbench Output's, in
  schema order and in the same shape. The sample is one quote: each input table with values
  under its name, an object for a one-row table and a list of its filled rows for a many-row
  one, rows with nothing typed left out and numbered as they stand for the index. Each value
  is as its column's type holds it: a number typed with a currency sign or separators is a
  number, an unticked box in a filled row is false, and a value that is not of its column's
  type stays as typed, for the Workbench Input to report.
- **The Workbench Input.** A pipeline takes the tables through the Workbench Input:
  `workbenchInput` in the pipeline document, declared in the pipeline file with
  `@pipeline.workbench_input(config=...)` (a submodel's file uses `@submodel.workbench_input`)
  and configured in `config/workbench_input/<function>.json`. In the editor it is "Workbench
  Input", badged "WORKBENCH IN", in the entry group with the Quote Input's colour and shape
  and an icon of its own. Its config has `tables` and `sample`, copies of the workbench's
  tables and sample quote.
  - **Read as a Quote Input.** A Workbench Input has one port per emitting table, named by the
    table's label, as a Quote Input with the same `tables` has, and a request is read through
    them exactly as through a Quote Input's: by `Pipeline.score`, a deployed pipeline's
    `/quote` and deploy's test quotes, none of which reads the sample. Deploy shares the Quote
    Input's defect
    [BUG-31](../roadmap/bugs.md#bug-31--a-deployed-quote-input-splits-a-request-into-its-tables):
    a deployed pipeline hands every port the whole request, so a Workbench Input's tables
    reach their ports in deploy only where a Quote Input's would. Haute calls the two the
    request inputs and decides this once, from one set holding both types; tests fail when a
    check in the code names the Quote Input alone where it means a request input. The
    pipeline never needs the workbench: the copies are an ordinary `tables` list and
    `sample` object.
  - **No file; the sample.** A Workbench Input reads no file. Without a request, in editor
    runs and previews and in generated code's `run()`, it reads its sample as one request is
    read through its tables: each emitting table is the rows the sample gives it, typed as
    declared, and a table the sample gives no rows, or every table when there is no sample,
    is one row of nulls, for a many-row table too. The sample is read whole, whatever a
    preview asks of it: every emitting table and every selected column, then cut to what is
    asked. What the tables do not read (fields under no table, unselected columns, tables
    that do not emit) is ignored, as a request's is, and a missing or `null` part reads as a
    request's does: as a many-row table's list it gives no rows, and as an object it leaves
    the columns under it null, keeping the row and its other values. Downstream nodes run on
    those rows, so a calculation previews on the sample's values, and a node that cannot take
    a null, such as a rating lookup on a null key, reports it in its preview as it would for
    any null input. The editor builds nothing for a Workbench Input: its tables have no input
    snapshots, a node that reads one of them as its data, such as Explore, reads its rows
    with nothing to cache, build or clear, and the cache report shows its tables together as
    current and read directly. Where deploy reads a Quote Input's sample file, to learn the
    request's schema and to dry-run the pipeline on one row, it derives both from a Workbench
    Input's tables, never its sample: the schema places each selected column where its path
    names, under an object for a one-row table and a list of objects for a many-row one, with
    its declared type, and the dry run reads one request record of nulls in that schema. A
    node that cannot take a null fails that dry run with its own error.
  - **One request input per pipeline.** A pipeline holds at most one Quote Input or Workbench
    Input, counting those inside its submodels. Saving or deploying a pipeline with two is
    refused with "Only one Quote Input or Workbench Input node is allowed per pipeline (found
    2).", deploy refusing before it prunes or builds anything, and `Pipeline.score` refuses to
    score one. While the pipeline has either, the palette greys out the request input it
    offers, titled "Only one Quote Input or Workbench Input allowed per pipeline", and dropping
    or pasting another is refused.
  - **The palette.** While the workbench is enabled, the palette shows the Workbench Input
    where the Quote Input was, and otherwise the Quote Input; never both. A Workbench Input
    dragged from it starts with the newest tables and sample fetched, or none while the first
    fetch is running, and gets them when it finishes. Enabling or disabling the workbench
    changes only what the palette offers: a pipeline keeps the request input it has, and a
    Quote Input becomes a Workbench Input, or back, only by deleting it and dragging in the
    other.
  - **Kept current.** The editor fetches the tables, with the sample, when it finds the
    workbench enabled and each time it adopts a pipeline document (opening one, or reloading
    it after a branch switch or a change on disk). Only the newest fetch counts. A fetch
    brings the Workbench Input of the document it was made for up to date, once, through the
    same update an edit in its panel makes, as one undoable step saved with the pipeline. A
    copy whose tables and sample both match is left alone, a missing sample matching an empty
    one, a read-only document is never changed, and while a submodel is open the update waits
    until the top level shows. Undo and redo restore earlier copies like any other edit, and
    the next fetch brings the copy up to date again. An update that does not commit, as when
    a submodel is opened while it is under way, is tried again when the top level shows. A
    fetch never changes a Quote Input, and a Workbench Input inside a submodel keeps the copy
    it has.
  - **Connections follow table names.** A connection to a table whose name the new tables
    still have stays, however the tables were reordered or replaced; one to a table whose name
    has gone is removed and reported, as removals are for any request input edit. A renamed
    table is a new port. A Quote Input's connections still follow a renamed table by position.
  - **Its panel** shows the tables read-only under "Tables from the workbench", with whatever
    the editor finds wrong with a table's name as a port (not an identifier, a reserved word,
    a repeat), and a note on what previews run on: "Previews run on the workbench's sample
    values; a table with none is one row of nulls." while the copy has a sample, and
    "Previews run on one row of nulls per table until the workbench supplies values." while
    it has none. While the workbench is not enabled the title is "Tables" and the panel says
    "The workbench is not enabled in haute.toml, so these tables are the last copy and
    nothing updates them.", and while a submodel is open "The editor updates these tables
    only at the pipeline's top level." A name another node already has is refused when the
    pipeline is saved, as it is for any request input. The Quote Input's panel is its table
    editor, whatever the project enables.
  - **The assistant** reads a Workbench Input and connects nodes to its tables, or removes
    such connections, as it does a Quote Input's, but cannot add, change, rename or delete one,
    nor edit its steps: its node card says the tables are the workbench's and the analyst adds
    the node from the palette.
- **The Workbench Output.** A pipeline fills the output tables through the Workbench Output:
  `workbenchOutput` in the pipeline document, declared in the pipeline file with
  `@pipeline.workbench_output(config=...)` (a submodel's file uses `@submodel.workbench_output`)
  and configured in `config/workbench_output/<function>.json`, whose `tables` are a copy of
  the workbench's output tables and whose `mapping` says which frame column fills which table
  column. In the editor it is "Workbench Output", badged "WORKBENCH OUT", in the exit group
  with the Quote Response's colour and shape and an icon of its own.
  - **A port per table.** A Workbench Output has one input port per table, named by the
    table's label, in the tables' order, and nothing downstream. A connection lands on one
    table's port: its `targetHandle` is the table's label, and the pipeline file says
    `pipeline.connect("<node>", "<workbench output>", target_port="<table>")`. A table takes
    one connection, and a node fills one table: its frame reaches the Workbench Output under
    the node's own name, as it reaches any node, so a second connection from the same node is
    refused, saying which table that node already fills.
  - **The mapping.** Each table column is filled from one column of the frame connected to
    its table's port: the frame's column of the same name, unless the mapping picks another or
    none. The mapping is `mapping` in the config, by table label and then column name, each
    entry a frame column's name or `null` for none; a column without an entry is filled by
    name. A column with no source, because the mapping says none or the frame has no column of
    its name, is filled with nulls, and the panel flags it. An entry naming a frame column the
    frame lacks fails the run, naming both. The fetch that updates the tables drops the entries
    of tables and columns the new tables no longer have.
  - **Its tables are its result.** When the node runs, its result is its tables, one frame per
    table under the table's label, each holding the table's columns in the tables' order with
    their declared types, and nothing else of the frames it was given. A column fits its
    declared type when it is of that type or null throughout, when it is any integer for an
    Integer column or any number for a Decimal one, and when it is categorical for a Text one.
    A one-row table holds exactly one row; a many-row table holds any number. Clicking the node
    previews its tables as dataframes, one at a time, chosen from the preview's table picker
    when there are several, the first table first. A value in them is traced from the node
    connected to its table: tracing one of the tables' own cells says so.
  - **The response.** The tables are what a priced quote's output tables are filled with. Where
    the pipeline answers a request, its tables become the response for one quote: a list of one
    object holding each one-row table as an object under its label and each many-row table as
    a list of objects under its label, each column at its path. The Quote Response's assembler
    builds it from the columns placed at their paths, so a null value or an empty list is left
    out as a Quote Response leaves it out, and two rows of a many-row table that agree on every
    column are one row, as in a Quote Response.
  - **A response node.** A Workbench Output is the pipeline's response as a Quote Response is:
    `Pipeline.run` and `Pipeline.score` return its response, deploy prunes the pipeline to it
    and takes the response's schema from it, and a deployed pipeline answers `/quote` with its
    response. Haute calls the two the response nodes. A pipeline holds at most one, counting
    those inside its submodels: saving or deploying a pipeline with two is refused with "Only
    one Quote Response or Workbench Output node is allowed per pipeline (found 2).",
    `Pipeline.run` and `Pipeline.score` refuse to run such a pipeline, and while it has either,
    the palette greys out the response node it offers, titled "Only one Quote Response or
    Workbench Output allowed per pipeline", and dropping or pasting another is refused.
  - **One quote per request.** A request read through a request input gives each one-row table
    one row per quote, and its many-row tables' rows say nothing of their quote, so a
    Workbench Output answers one quote: a request of several fails it on its first one-row
    table, and a pipeline whose Workbench Output has only many-row tables puts every row of
    every quote under one.
  - **The palette.** While the workbench is enabled, the palette shows the Workbench Output
    where the Quote Response was, and otherwise the Quote Response; never both. A Workbench
    Output dragged from it starts with the newest tables fetched, or none while the first
    fetch is running, and gets them when it finishes. Enabling or disabling the workbench
    changes only what the palette offers: a pipeline keeps the response node it has.
  - **Kept current.** The fetch that keeps each Workbench Input current brings each Workbench
    Output's tables up to date at the same moments and in the same way: once per fetch, through
    the same update an edit in its panel makes, as one undoable step saved with the pipeline,
    never in a read-only document, and only while the top level shows. A copy whose tables
    match is left alone, a Workbench Output inside a submodel keeps the copy it has, and a
    fetch never changes a Quote Response.
  - **Connections follow table names.** A connection to a table whose name the new tables
    still have stays, wherever the table has moved; one to a table whose name has gone is
    removed and reported, as for a Workbench Input. A renamed table is a new port.
  - **Its panel** shows each table: its name, whether it has one row per quote or many, the
    node connected to it or "Not connected", and whatever the editor finds wrong with its name
    as a port; then, for each of its columns, the column's name and type and a choice of the
    connected frame's columns that fills it (the columns its last preview recorded), showing a
    same-named column as chosen "by name" and flagging a column nothing fills. Changing a choice
    is an edit to the pipeline like any other. The tables themselves are read-only: the panel has
    the Workbench Input panel's title and its notes for when the workbench is not enabled and
    while a submodel is open.
  - **The assistant** reads a Workbench Output and connects nodes to its tables, or removes
    such connections, but cannot add, change, rename or delete one: its node card says the
    tables are the workbench's and the analyst adds the node from the palette.

## Design rationale

- **A project setting, not an installed package.** Whether a project's quotes come through a
  workbench is a fact about the project, so it is recorded with the project's other facts in
  `haute.toml`, versioned and reviewed with them, and every clone of the project agrees. The
  workbench was first a separate package, Obverse, that Haute found through a Python
  entry-point group and never imported by name, so that installing the package was the whole
  switch; that made the switch a fact about a machine's environment rather than about the
  project, left Haute hosting a second React and a second stylesheet in shadow roots to share
  one page, and kept two copies of the kit the two toolbars were built from. One built-in
  workbench with one switch replaces it.
- **The form is a project file.** The pipeline's quote tables come from the form, so the form
  belongs beside the pipeline, in the project and in its history, where a reviewer sees the
  schema change that changed the pipeline's ports. Haute reads it from disk on every request
  that needs it rather than caching it, so the standalone builder's save, a hand edit and a
  branch switch each reach the next fetch without a restart or a stale copy.
- **Read per request, fail where it can be fixed.** The status and tables routes read
  `haute.toml` and the form each time. A bad `[workbench]` table or an invalid form
  answers 409 with what to fix, which the editor shows as a toast, and the pipeline editor goes
  on working; a server that refused to start would hide the pipeline for a typo in the
  workbench's settings.
- **One view host, natively.** The view is shown the way the extension host showed
  Obverse's: the pipeline editor stays mounted, so nothing it holds is lost, and only its
  keyboard and React Flow's document-level keys are fenced. A second React or a shadow root
  bought nothing once the view was Haute's own, so the host returned natively with it.
- **One revision rule.** The form's revision is the content hash of its file's bytes, as
  the pipeline's manifest hashes its files, and a stale save answers the pipeline save's
  code, so the editor matches one refusal. The bytes are hashed rather than the parsed
  form, so a hand edit that changes only whitespace still counts as a change: the view
  read other bytes, and a save over them must say so.
- **The blank form is a form.** A project whose workbench was enabled by hand has no file
  yet, and the view is where its first form is made, so an absent file reads as the blank
  form with no revision rather than as an error, and the first save creates it.
- **Commit on blur.** The schema editor's fields commit as Haute's editors commit, once
  per edit, so an undo step is an edit rather than a keystroke and no timer decides where
  one edit ends and the next begins.
- **A gesture is one step.** A drag records the form when the pointer first moves and
  replaces it on each move without history, as the graph store's raw setters do for the
  canvas, so undo reverses the whole drag; a burst of nudges is grouped the same way.
- **One reorder.** The step editor's cards and a component's fields are dragged into
  order through one hook, a row dropped on another put there, rather than a second drag
  model with markers of its own.
- **The view's state is the view's.** Which sheet and section show, the selection, the
  zoom and the panel's width are neither saved with the form nor undone with it: they
  live in a store of their own, read against the form, so a sheet or a component that
  disappears from the form is simply no longer the one showing or selected.
- **Canonical only.** The form has one shape. A field is always written, so a file never says
  one thing by presence and another by absence, and an unknown field is refused by name rather
  than carried or dropped; nothing migrates an older spelling, as the repository's
  canonical-only policy requires.
- **A type of its own.** A Quote Input whose tables the workbench supplied did a different job
  from one whose tables are built in its panel, yet both read the same in the pipeline file and
  the editor branched on the difference everywhere. The Workbench Input says in the pipeline
  file where its tables come from, and the Quote Input keeps one job.
- **One way of reading a request.** The two types read a request identically, so their
  behaviour is decided from one set rather than copied: a change to how a request is read,
  such as the fix for BUG-31, covers both, and a test reports a check that names the Quote
  Input alone.
- **A copy, not a reference.** The pipeline file is canonical and runs without the GUI, so a
  Workbench Input holds the tables it runs from instead of pointing at the form: runs, tests,
  generated code and deploy never read the form, and the editor, which has both, keeps the
  copy current.
- **The workbench's sample, not a file.** Quotes keyed in through a workbench leave no sample
  file, and the workbench is where the quote's tables are laid out, so it is where a pricing
  analyst types the quote previews run on. Copied into the config like the tables, it lets
  the pipeline file run and preview without the form; read as a request is, it gives
  the rows a request would; read whole, every reader finds the same misfit, and resolving
  points, which reads only the tables, never does. A table it gives no rows is one row of
  nulls rather than none, so every node's expressions still run on a row and its preview
  shows one. Deploy takes the request's shape from the tables, as a deployed pipeline never
  sees the sample.
- **Connections follow names.** The form may reorder or replace its tables wholesale, so a
  table's position says nothing about which table it is, and matching by position would
  quietly reconnect a downstream input to another table. A table's name is also its key in
  the request.
- **Fetched for a document.** A fetch belongs to the document it was made for, and fetches can
  settle out of order, so only the newest publishes and none applies to a document adopted
  after it started, including through an update still waiting on identity resolution. Graph
  edits, undo and redo never start one, so the refresh never fights the user's history.
- **The response's tables, filled through a mapping.** The workbench defines what a priced
  quote fills in as it defines the quote, so the pipeline's part is to connect a frame to each
  table and say which of its columns fills which. Filling by name until the analyst picks
  otherwise keeps a pipeline built to the workbench's names free of mapping, and a column
  nothing fills is visible in the panel and the preview rather than stopping every run while
  the pipeline is being built.
- **Tables, not a document.** The workbench's output tables are tables, and a pricing analyst
  checks them as tables, so the node's result is its tables and its preview a dataframe per
  table, as a Workbench Input's is per input table. The response document is how the tables
  travel in a request's answer, so it is built where the pipeline answers one.
- **One assembler.** The Workbench Output's response places each column at its path and hands
  them to the Quote Response's assembler, so the two response nodes build, prune and type a
  response alike, and deploy serves either unchanged.
- **One quote per request.** A request's quotes carry no identity into their tables' rows, so
  the rows of several quotes cannot be told apart once they are tables. A workbench prices
  one quote at a time, so a Workbench Output answers one, and a one-row table with other than
  one row fails rather than joining rows by position.
- **Ports are the tables, inputs are the nodes.** Every node receives a frame under its
  source's name, and codegen, parsing, projection and tracing rely on it, so the table a frame
  fills is the connection's target port, as an Edge Join's role is, rather than its input
  name. The price is that a node fills one table.

## Interactions

- [server-api](../server-api/high-level.md): `haute.server` includes the workbench router
  with its feature routers and owns the response and request models; the application's
  exception handlers answer a workbench problem as 409, and a stale save answers the
  pipeline save's `stale_document_revision` code.
- [cli](../cli/high-level.md): `haute init --workbench` writes the table and the blank form.
- [pipeline-config](../pipeline-config/high-level.md): the `[workbench]` table in the shared
  `haute.toml` schema, the starter form among the scaffold's templates, and the
  `workbench_input` and `workbench_output` decorators and their config folders.
- [deploy](../deploy/high-level.md): `DeployConfig.from_toml`'s whole-file check accepts the
  `[workbench]` table; save and deploy refuse a second request input or response node, deploy
  takes a Workbench Input's request schema and sample record from its tables, and a deployed
  pipeline answers with a Workbench Output's response as with a Quote Response's.
- [git-integration](../git-integration/high-level.md): an unborn repository's seed commit
  takes `forms/` with the pipeline's files.
- [sandbox-security](../sandbox-security/high-level.md): the local Host, Origin and session
  middleware gate the workbench routes.
- [frontend-shared](../frontend-shared/high-level.md): the API client validates the three
  workbench responses with the generated contract; the workbench's toolbar is built from
  the kit's brand, Undo/Redo and Zoom In/Zoom Out and the project's controls the pipeline
  toolbar ends with; its palette is the kit's palette shell, whose reveal strip takes a
  label; its properties panel is the kit's side panel; the schema editor's and the panel's
  fields are the shared form primitives (the committed and validated text fields, the
  checkbox and the icon select); and the fields' order uses the list-reorder hook the step
  editor's cards use.
- [engineering-quality](../engineering-quality/high-level.md): the generated contract bundle
  carries the `workbench` response group.
- [json-shredding](../json-shredding/high-level.md): the v2 tables the form defines, checked by
  the Quote Input's schema rules and read as a Quote Input's are, its sample read through them
  in memory, and the Quote Response's assembler building a Workbench Output's response.
- [frontend-node-editors](../frontend-node-editors/high-level.md): the palette's Workbench
  Input and Workbench Output and their panels.
- [frontend-graph-canvas](../frontend-graph-canvas/high-level.md): the editor shell hosts
  the view, hiding and fencing the pipeline editor while it shows, and its keyboard
  shortcuts register nothing then; a palette drop reports the node it creates, the commit
  controller checks an update's `isCurrent`, the request inputs share one singleton slot
  and the response nodes another, and a Workbench Output's connections land on its
  tables' ports.
- [caching](../caching/high-level.md): a Workbench Input's `tables` and `sample`, and a
  Workbench Output's `tables`, are classified as node config, and a Workbench Input's tables
  have no input snapshots.
- [assistant](../assistant/high-level.md): the Workbench Input's and Workbench Output's node
  cards and the operations that refuse to author either.

## Failure model

- A `[workbench]` table that is not a table, lacks `enabled`, gives `enabled` something other
  than `true` or `false`, gives `form` something other than a non-empty relative path inside
  the project, or holds any other key, and a `haute.toml` that cannot be read, is not UTF-8
  text or cannot be parsed when a workbench route reads it, each fail that route with 409 and
  a message naming the key or the file and what to set. The default form path is held to the
  same rule: a `forms` folder that is a symlink or junction to somewhere outside the project
  is refused like a configured path that escapes it. The editor shows the message as one error toast and goes on; the
  table is read again on the next request. `haute deploy` refuses an unknown key under
  `[workbench]` as it refuses any unknown `haute.toml` key.
- While the workbench is enabled, a form file that cannot be read, is not UTF-8 text, is
  not JSON or does not fit the form's shape fails `GET /api/workbench/tables` and
  `GET /api/workbench/form` with 409 naming the file and the first thing wrong with it; a
  file that does not exist is the blank form. A fetch of the tables that fails shows one
  error toast and changes no Workbench Input or Workbench Output; a fetch made for an
  earlier document never changes the current one, and an update it started that is still
  waiting on identity resolution is dropped with the commit controller's usual message. A
  read of the form that fails is shown in the view in place of the schema editor, with a
  way to try again.
- `PUT /api/workbench/form` answers 404 while the workbench is not enabled; 409
  "stale_document_revision: The workbench's form changed on disk after the workbench read
  it. Reload the workbench before saving." when the file's revision is not the
  `base_revision` quoted (a file that appeared since a first save included), writing
  nothing; and FastAPI's 422 for a body that is not a form, as any typed body answers. The
  view reports a stale refusal in a toast and a banner, keeping the edits until Reload, and
  any other failed save in one error toast naming the cause.
- A sheet's components are deleted with it, after a confirmation; the last sheet cannot
  be removed, and the view never offers to. A layout value outside its range (a Table's
  rows 1 to 50, a Collection's columns 1 to 12) is refused at the field, which says the
  range. A component dropped outside the sheet's viewport is not added.
- The schema editor refuses nothing: a problem is shown beside its table, and a save
  writes the form as it is. The tables route then answers the structured 422 for a table
  the Quote Input's rules refuse, which the tables fetch shows as one toast, so the
  problem is visible in both views until it is fixed.
- `GET /api/workbench/tables` answers 404 while the workbench is not enabled; the editor
  never asks then. Tables or response tables that break the Quote Input's schema rules, such
  as a table named with a Python keyword, answer the structured 422 that Infer Tables answers.
- A failed status request shows one error toast; the editor behaves as it does with the
  workbench not enabled.
- A Workbench Input or Workbench Output in a project whose workbench is not enabled keeps its
  copy and runs from it; its panel says so and nothing updates it.
- A Workbench Input with no tables has no ports. Previewing it fails with "This Workbench
  Input has no tables: add input tables to the workbench's schema and save it.", and
  deploying it fails because its request has no schema. Deploy also fails, naming the field,
  when two of its columns' paths disagree about a request field.
- A sample that does not fit its Workbench Input's tables fails whatever reads its rows
  (previews, `run()`, leasing a table point), and planning a preview that sizes the tables,
  with "The workbench's sample does not fit this Workbench Input's tables: <reason>. Correct
  it in the workbench and save it." It does not fit when a value is not of its column's
  declared type, when something other than a list stands where a many-row table's rows
  belong, when something other than an object stands where a one-row table's object, or any
  object a column's path passes through, belongs, or when a many-row table's list holds
  anything but rows (a value, `null` or a list). Resolving a table point and the cache report
  read the tables alone, so a misfit sample never stops them.
- Two request inputs of either type, at the top level or in a submodel, fail save and deploy
  with "Only one Quote Input or Workbench Input node is allowed per pipeline (found 2)." and
  `Pipeline.score` as two Quote Inputs do. A config key a Workbench Input does not declare,
  `path` among them, is refused as it is for any node type.
- Running a Workbench Output, in a preview, `run()`, `score()` or a deployed pipeline, fails
  with a message naming its table:
  - with no tables, "This Workbench Output has no tables: add output tables to the
    workbench's schema and save it.";
  - for a table nothing is connected to, "Connect a frame to the Workbench Output's
    '<table>' table.";
  - for a connection to a port that is no table of the node's;
  - for a mapping entry naming a column its table's frame lacks, naming the frame's columns,
    and for a column whose type does not fit, naming both types;
  - for a mapping entry for a table or column the node does not have;
  - for a one-row table whose frame has other than one row, saying how many it has.
- Two response nodes of either type, at the top level or in a submodel, fail save and deploy
  with "Only one Quote Response or Workbench Output node is allowed per pipeline (found 2).",
  and `Pipeline.run` and `Pipeline.score` refuse the pipeline, naming both.
