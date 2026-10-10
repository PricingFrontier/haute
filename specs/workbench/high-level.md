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
Haute (its host beside the pipeline editor, its toolbar and shortcuts, its schema editor,
its sheets, the sample typed into them and priced live, and Preview, the sheets as an
underwriter uses them, with a quote checked against the columns' rules and priced), and
the two workbench node types: where their tables come from, how the editor keeps them
current, what a Workbench Input gives without a request and how a Workbench Output's
frames fill the response.

## Scope

In scope:

- The `[workbench]` table of `haute.toml`: whether the project's workbench is enabled and
  where its form is.
- The form file: its canonical shape, how it is read and written, and the blank form
  `haute init --workbench` scaffolds.
- The workbench's tables as the pipeline holds them: the schema's input tables, each its
  name, its rows per quote and its typed columns, the sample quote as a request holds it,
  and the output tables in the same shape.
- `GET /api/workbench`, the status the editor reads, `GET /api/workbench/tables`, the
  tables, sample and response tables as the saved form defines them now, and
  `POST /api/workbench/tables`, the same of a form the view holds, saved or not.
- `GET /api/workbench/form`, the form with its file's revision, and
  `PUT /api/workbench/form`, which writes the form unless the file changed since it was read.
- The Workbench view: how it is shown beside the pipeline editor and what the pipeline
  editor does meanwhile, its toolbar and keyboard shortcuts, its schema editor, its sheets
  (the canvas, the Table and the Collection, their palette and properties panel, and the
  sheet tabs), the sample typed into the components and priced live on the pipeline,
  Preview, where an underwriter's quote is keyed into the sheets, checked against the
  columns' rules and priced, and how the form is read into it and saved from it.
- The Workbench Input, the node type that holds a copy of the tables and sample, which the
  editor keeps current and shows, and previews on.
- The Workbench Output, the node type that holds a copy of the output tables, which the
  editor keeps current and shows, and whose ports take the frames that fill them.

Out of scope:

- The toolbar's pipeline controls ([frontend-shared](../frontend-shared/high-level.md)) and
  the canvas, palette and keyboard shortcuts
  ([frontend-graph-canvas](../frontend-graph-canvas/high-level.md)).
- The Host, Origin and session-cookie checks
  ([sandbox-security](../sandbox-security/high-level.md)), which apply to the workbench
  routes exactly as to any other `/api/` route.
- How a deployed pipeline reads a request into its tables ([deploy](../deploy/high-level.md)).
- The app that shows the sheets to an underwriter outside Haute and prices through the
  deployed pipeline ([WB-07](../roadmap/workbench.md#wb-07--the-underwriter-app)). Preview
  is those sheets inside the builder, priced on the pipeline open in the editor.
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
  from 1. A sheet has an id, a title and its components (`widgets` in the file): a Table (`tableInput`, a grid of at
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
  response tables are checked as the pipeline takes them (a table's name a port's, a column's
  name a frame column's, no name twice) and a breach answers the structured 422 of a public
  contract error; the sample is served unchecked, since a sample that does not fit fails the
  previews that read it and never stops the tables updating.
  `POST /api/workbench/tables` answers the same for a form sent in the request, saved or
  not, checked the same way and 404 while the workbench is not enabled alike; it writes
  nothing.
- **The form, served and saved.** `GET /api/workbench/form` answers the form as its file
  holds it now and the file's `revision`, the content hash of its bytes: `null` while the
  file does not exist, when the form is the blank one. `PUT /api/workbench/form` takes the
  whole form and the `base_revision` it was read at, writes the file (every field, in its
  canonical spelling) under the pipeline save's lock, as every write to the project is, so
  two saves never pass the revision check together, and answers the form with its new
  revision. The written file is one
  save on the clone's save ledger, captured as the pipeline's save captures the files it
  wrote and through the same capture: no working branch, no capture; a missing git
  identity leaves the file uncaptured and is reported for the editor to ask for; a capture
  that fails is a warning, and leaves the file uncaptured too. The response says what the
  capture gave (`git_sha`, `warnings`
  and `identity_required`, as the pipeline save's response has them), and from its first
  captured save on the file is tracked, so a milestone's sweep takes an edit made to it by
  hand. A file whose revision is no longer `base_revision`, edited by hand or by a branch
  switch, is left as it is and the save is refused with 409 and a `stale_document_revision`
  detail, as the pipeline's save refuses a stale document; a first save (`base_revision`
  `null`) creates the file unless one has appeared. Both routes answer 404 while the
  workbench is not enabled.
- **The view.** While the workbench is enabled, a view switcher at the bottom of the left
  palette offers "Pricing", the pipeline editor, and "Workbench".
  - **Showing it.** The Workbench view covers the area below the toolbar: its own left
    column, the component palette with the switcher back, and the section the toolbar
    chooses. Meanwhile the pipeline editor stays mounted (live sync, the document and its
    undo history go on) but invisible and inert, its keyboard shortcuts, Ctrl/Cmd+Enter and
    React Flow's delete and pan keys off and its floating menus closed, so a key pressed in
    the view never edits the hidden pipeline. The Git and Assistant panels open beside the
    view, in the properties panel's place. The Workbench Input's and Workbench Output's
    panels offer "Edit in Workbench", which shows the view.
  - **The toolbar** keeps the brand and the project's controls (Assistant, Help, the branch,
    Save and Commit) and shows the view's own, by mode:

    | Mode | The view's controls |
    |---|---|
    | Building or Preview | Build over Preview |
    | Building, the sheets showing | Sheets over Schema, the form's Undo and Redo, Zoom In over Zoom Out, and why pricing the sample last failed |
    | Building, the schema showing | Sheets over Schema, and Undo and Redo |
    | Preview | Price over Clear for the quote (both disabled while nothing is typed in the tables the schema has now), Zoom In over Zoom Out, and why the quote has no price |

  - **Save.** Save and Ctrl/Cmd+S save the project, the same from either toolbar, with a
    focused field's edit included: the form is saved first when it holds unsaved edits or
    a save the ledger did not capture (for want of an identity, or because the capture
    failed), the Workbench Input's and Workbench Output's copies follow it, and then the
    pipeline is saved, so what is saved runs on the form that was saved. The tables are
    fetched afresh for that, and a fetch that fails refuses the pipeline's save, with a
    toast saying the pipeline was not saved, rather than record copies behind the form. A
    form save refused, as stale or failed, saves no pipeline, so the pipeline never carries
    copies of a form the view does not show; and a save of a pipeline document that cannot
    be saved (read-only, or changed on disk under unsaved edits), or one asked for inside a
    submodel, is refused before the form is saved, with the pipeline save's own reason, so
    the form is never saved ahead of a pipeline that is not. Save passes the pipeline
    editor's git readiness gate first, as it does there: with no working branch chosen, or
    a divergent one, the gate's modal opens and the save follows its confirmation. Each
    file's save is reported as the pipeline's is: "Saved → forms/form.json" and the
    pipeline's own toast, the ledger commit on the branch indicator, each warning in a
    toast of its own, and the git-identity prompt when the capture waits on one, once per
    session. Which view shows never decides what a save records.
  - **Commit** runs the project's milestone flow, the same from either toolbar: the project
    is saved as Save saves it, and then the milestone is asked for, so what the milestone
    records runs on the form it records; a save refused asks for no milestone. The identity
    prompt's retry, a move's Save first and the Git panel's save before a switch save the
    project the same way.
  - **Guards.** A move, a branch switch, an archive or a delete that would replace the
    working tree asks about unsaved edits in the form as it asks about the canvas, and a
    switch chosen over them reads the destination's form in their place once it has
    succeeded.
  - **Reading the form.** The view reads the form when it first shows and keeps it, with
    its unsaved edits and history, across a trip to the pipeline editor, and reads the file
    again whenever the pipeline editor adopts its document anew, as a branch switched by
    hand or a change on disk brings: a file at the same revision changes nothing; a changed
    one replaces the form, its history dropped, while the form is as saved, and otherwise
    the view says the file changed on disk and offers Reload. A document adopted while the
    file is still being read is read again after it, so the form never ends on the branch
    the read began on. A move reloads the page, and the view reads the form anew when it
    next shows; a switch in place from the Git panel reads the destination's form through
    that adoption, or at once when the switch was chosen over the form's unsaved edits. A
    form that cannot be read is reported in the view, with what is wrong and a way to try
    again.
- **The schema editor.** The form's tables, each with its name, its role (Input, sent to
  the pricing engine, or Output, returned by it), its rows (One row per quote, or Many
  rows) and its columns; each column with its type, drawn as the step editor's marker for
  the kind (a number, text, true/false, a date) in the data preview's colour and changed
  from it, its name and, in a many-row table, whether it is a key. A column's label and, in
  an input table, its rules open beside it: required, a range for an Integer or a Decimal,
  and the allowed values, typed comma-separated, for any type but True/false and Date.
  Changing a column's type drops the rules the new type cannot have. Every text field
  commits on blur or Enter, so an edit is one undo step; Enter in a column's name adds the
  next column, Alt+Up and Alt+Down move one, and a new table's or column's name takes the
  focus. A many-row table can have an index: a column first in the table, `row_number`
  until renamed, an Integer whose type cannot change, numbering the rows from 1, which
  Alt+Up and Alt+Down neither move nor pass; switching a table to one row clears its keys
  and drops its index, with what shows it. Removing a column or a table a sheet shows asks
  first, and takes it off the sheet and out of the sample. The editor says what would stop
  the schema being the pipeline's tables: a table's name is held to the port rules the
  Quote Input's labels are held to (an identifier, none of the document's reserved labels,
  unique whatever its case), a column's name is an identifier that is not a Python keyword,
  as the server holds it, unique in its table, a many-row table needs a key, an input
  column's range must not be inverted and its allowed values must be of its type, and
  True/false and Date columns can have none.
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
  move recording nothing. Pressing the empty sheet deselects. A component takes the
  keyboard's focus too: reached by Tab, it is selected, so the sheet's keys work without a
  pointer. A component with a problem (no fields, a column no longer in the schema, a table
  of the other kind, tables whose rows do not line up) gets a dashed frame in the warning
  colour naming the problem, on hover and to a screen reader. The zoom runs from 25% to
  200% in steps of 10%; what is on the sheet is fitted to the viewport, never past 100%,
  when the sheets first show for a form read (a zoom chosen since is kept across a trip to
  the schema or the pipeline editor) and on Ctrl/Cmd+1.
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
  they are closed: the component's kind and its title; once it shows a field, its layout,
  a Table's rows (1 to 50) or a Collection's columns across (1 to 12), a value outside the
  range refused at the field, and the order of the fields it shows, moved by dragging or
  Alt+Up and Alt+Down, each with a cross that stops showing it; and its fields, ticked from
  the schema tables of its
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
- **The sample.** Typing into a component's input columns while building types the form's
  sample. A Collection's boxes hold its one-row tables' one row; a Table's grid holds its
  many-row tables' rows, row by row, with Add row and a delete on each row adding and
  deleting a row in every input table the grid shows at once, so their cells stay side by
  side; a one-row table's column in a grid shows that table's one value on every row, and a
  grid shows at least its rows. Each cell is the control its column calls for: a tick box
  for True/false, a dropdown of the allowed values, a date picker, else a text field, a
  number's right-aligned. A text field commits on blur or Enter, a dropdown, a tick box and
  a date on change, each commit one undo step. Values are held as typed, text or a tick, by
  table id and column id; the server types them by their columns when the tables are made.
  Pressing a cell types there and selects its component rather than moving it. The sample
  is part of the form: saved with it, undone like any edit, and dropped with a column or
  table removed from the schema.
- **Priced live.** While the view shows, the sample is priced on the pipeline open in the
  editor whenever the schema or the sample changes, once typing pauses for 300ms, and when
  the sheets show, as the pipeline may have changed meanwhile; laying the sheet out
  differently prices nothing. A pricing asks `POST /api/workbench/tables` for the tables
  and sample Haute takes from the form as it stands, then previews the Workbench Output a
  table at a time through the preview route on the document as the editor holds it, the
  whole pipeline even while a submodel is open, with the active source: the document's
  top-level Workbench Input and Workbench Output take the form's tables and sample for that
  request alone, as a fetch gives them, a workbench node inside a submodel keeps its copy,
  and the document itself is untouched. One pricing runs at a time; asked again meanwhile,
  it prices once more when it finishes, on the form as it is then. A Collection's output
  column shows its table's value in the answer, formatted as the data preview formats a
  value, dimmed while a newer answer is on its way, and a dash before the first answer, for
  a column nothing filled, and after pricing fails, when the toolbar says why ("Pricing
  failed: …") until a pricing succeeds. A Table's output column shows, on each grid row,
  the value of the row in the answer keyed like it: the output table's key columns hold
  what the server typed for the grid row's (the index numbered as the grid numbers the
  row), found through the first many-row input table the grid shows that is keyed alike;
  a grid row with nothing typed, or none keyed like it, shows a dash, and a price for other
  values is dimmed on every row.
- **Preview.** The toolbar's Build over Preview switches the view between building and
  the sheets as an underwriter uses them: the same sheets at the same positions, their
  tabs only switching sheets, the components neither selected nor moved, the palette's
  components disabled, no properties panel, no Undo or Redo, and the zoom. An
  underwriter's quote is keyed into the components' input cells as the sample is, through
  the same controls, and is kept apart from the sample: it lasts while the editor is open,
  through Build and back, is never saved with the form and never undone. Price is disabled
  while nothing is typed in the input tables the schema has now (a value typed in a column
  since removed, or in a second row of a table now with one row per quote, counts for
  nothing, as the server leaves it out of the quote), and refuses
  such a quote with "Type the quote first" however it is asked: a deployed request never
  holds a blank quote, and the server would read one as the null quote. Price checks the
  quote against the columns' rules first: a required cell left empty, text that is not a
  number in a number column or not a whole number in a whole-number column, a number
  outside the column's range, a value that is not one of the allowed values; a one-row
  table's one row is always checked, a many-row table's filled rows only, and a tick box
  and the index never fault. A quote that breaks a rule is not priced: each such cell is
  outlined, naming its problem on hover, the toolbar says how many cells need attention,
  and from then on the marks follow the cells as they are edited, until the quote is
  cleared. A quote that passes is priced as the sample is, on the pipeline open in the
  editor with the quote in the sample's place, read as a request is, so it prices as it
  would deployed, and every output column shows its value, a
  Collection's its table's one row and a Table's the row keyed like each grid row, dimmed
  once the quote changes until Price again; a pricing that fails shows its reason in the
  toolbar. Clear, after a confirmation, empties the quote, its marks and its price, and an
  answer to a pricing still on its way is dropped, as one is once the view has left.
  Ctrl/Cmd+S still saves the project in Preview and Ctrl/Cmd+1 fits the sheet; the sheet's
  editing keys and undo do nothing.
- **Saved.** A successful save of the form adopts the file's new revision; the project
  save then fetches the tables afresh, so the Workbench Input's and Workbench Output's
  copies follow the schema, as after any fetch, and saves the pipeline with them. A save refused
  as stale keeps the edits and says so, in a toast and in a banner in the view whose
  Reload reads the file again, dropping the edits and the history.
- **The tables as the pipeline holds them.** The input tables are the Workbench Input's, in
  schema order: each table its name, `one` row per quote or `many`, and its columns, each its
  name and its type, the index column an Integer. The form's keys, labels, rules and allowed
  values stay in the form: the pipeline has no use for them. In each quote a one-row table is
  an object under its name and a many-row table a list of objects under its name; a request
  is one quote. A table with no columns yet is not a port: there is nothing to read into it.
  The output tables are the Workbench Output's, in schema order and in the same shape. The
  sample is one quote: each input table with values
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
  - **Read as dataframes.** A Workbench Input has one port per table with a column, named by
    the table, and reads a quote into them with no JSON path: a one-row table is its one row
    (of nulls when the quote does not hold it) and a many-row table the rows the quote holds.
    Each value is read as its column's type holds it: a whole number for an Integer, a
    number for a Decimal, text for Text, true or false, and a `YYYY-MM-DD` date; a value that
    is not, a part of the quote that is not of its table's shape, or a quote that is not an
    object, is refused naming the spot. A key the tables do not name is not read, and a column
    a row does not hold is null. `Pipeline.score` takes frames already split per table; a
    deployed pipeline's `/quote`, deploy's test quotes (each case scored as a request of its
    own) and its dry run read the request through the same reader, one quote per request,
    each table under its name, from the records as they were sent (a frame built from them
    by inference would type them first, and refuse a misfit as Polars does rather than as
    the input does), so a deployed Workbench Input gives each port its table where a
    deployed Quote Input still hands every port the whole request
    ([BUG-31](../roadmap/bugs.md#bug-31--a-deployed-quote-input-splits-a-request-into-its-tables)).
    The memory estimate of a deployed run sizes each consumer from the table that feeds it,
    not from the one-row request that holds them all. None of them reads the sample. Haute calls the two the request inputs and decides this
    once, from one set holding both types; tests fail when a check in the code names the Quote
    Input alone where it means a request input. The pipeline never needs the workbench: the
    copies are an ordinary `tables` list and `sample` object.
  - **No file; the sample.** A Workbench Input reads no file. Without a request, in editor
    runs and previews and in generated code's `run()`, it reads its sample as it reads a
    request, so the sample prices as the quote it stands for would when deployed: each port
    is the rows the sample gives its table, typed as declared, a many-row table the sample
    does not hold has no rows, and a one-row table it does not hold is its one row of nulls.
    While there is no sample (none, or nothing typed in it), every table is one row of
    nulls, the null quote, so the pipeline previews before the workbench supplies values.
    The sample is read whole, whatever a preview asks of it: every port and every column,
    then cut to what is asked. What the tables do not read (keys under no table, columns no
    table declares) is ignored, as a request's is. Downstream nodes run on
    those rows, so a calculation previews on the sample's values, and a node that cannot take
    a null, such as a rating lookup on a null key, reports it in its preview as it would for
    any null input. The editor builds nothing for a Workbench Input: its tables have no input
    snapshots, a node that reads one of them as its data, such as Explore, reads its rows
    with nothing to cache, build or clear, and the cache report shows its tables together as
    current and read directly. Where deploy reads a Quote Input's sample file, to learn the
    request's schema and to dry-run the pipeline on one row, it derives both from a Workbench
    Input's tables, never its sample: the schema holds each table under its name, an object of
    its columns for a one-row table and a list of them for a many-row one, with their declared
    types, and the dry run reads one quote with nothing filled in, one row of nulls per table,
    the rows a preview runs on before the workbench supplies values. A node that cannot take a
    null fails that dry run with its own error. Deploy's test quotes are not held to the
    tables as a Quote Input's are to its columns: a quote may leave a table out, and the
    reader says what does not fit when it is scored; their expected outputs are compared
    into the response's tables, a number inside one held to the tolerance and a boolean to a
    boolean. A Databricks deployment's MLflow signature holds each table as a map of any
    values, or an array of them, a table allowed to be left out of a request, since a quote
    may carry columns the tables do not name, which the reader leaves unread and which
    MLflow's typed object would refuse; the table's columns are in the manifest's input
    schema, and the reader holds a served request to them as it holds the container's,
    reading the records MLflow passed rather than a frame inferred from them, which a
    column of mixed types the tables do not name would fail.
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
    values, read as a request is: a table with many rows and nothing typed has none." while
    the copy has a sample, and
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
  - **A port per table.** A Workbench Output has one input port per table with a column,
    named by the table, in the tables' order, and nothing downstream. A connection lands on
    one table's port: its `targetHandle` is the table's name, and the pipeline file says
    `pipeline.connect("<node>", "<workbench output>", target_port="<table>")`. A table takes
    one connection, and a node fills one table: its frame reaches the Workbench Output under
    the node's own name, as it reaches any node, so a second connection from the same node is
    refused, saying which table that node already fills.
  - **The mapping.** Each table column is filled from one column of the frame connected to
    its table's port: the frame's column of the same name, unless the mapping picks another or
    none. The mapping is `mapping` in the config, by table name and then column name, each
    entry a frame column's name or `null` for none; a column without an entry is filled by
    name. A column with no source, because the mapping says none or the frame has no column of
    its name, is filled with nulls, and the panel flags it. An entry naming a frame column the
    frame lacks fails the run, naming both. The fetch that updates the tables drops the entries
    of tables and columns the new tables no longer have.
  - **Its tables are its result.** When the node runs, its result is its tables, one frame per
    table with a column, under the table's name, each holding the table's columns in the tables' order with
    their declared types, and nothing else of the frames it was given. A column fits its
    declared type when it is of that type or null throughout, when it is any integer for an
    Integer column or any number for a Decimal one, and when it is categorical for a Text one.
    A one-row table holds exactly one row; a many-row table holds any number. Clicking the node
    previews its tables as dataframes, one at a time, chosen from the preview's table picker
    when there are several, the first table first. A value in them is traced from the node
    connected to its table: tracing one of the tables' own cells says so.
  - **The response.** The tables are what a priced quote's output tables are filled with. Where
    the pipeline answers a request, its tables become the response for one quote: a list of one
    object holding each one-row table as an object under its name and each many-row table as a
    list of objects under its name, each column under its name, as a quote holds its tables.
    The response is the tables as they are, every row and every column, and where it is
    rendered as JSON a null value or an empty list is left out, as a Quote Response's are.
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
    Workbench Output answers one quote: a deployed pipeline refuses a request of several
    quotes at its Workbench Input first ("A Workbench Input reads one quote per request, and
    this request holds 2."); a frame that reaches a one-row table with other than one row,
    as `Pipeline.score` with split frames or a Quote Input upstream can bring, fails it on
    that table, and a pipeline whose Workbench Output has only many-row tables puts every
    row of every quote under one.
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
  that needs it rather than caching it, so a hand edit and a branch switch each reach the
  next fetch without a restart or a stale copy.
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
- **One more file on the ledger.** The form is captured through the pipeline save's own
  capture rather than a capture of its own, so one place decides what a save on the ledger
  means: no working branch, a missing identity, a failed capture. Commit's sweep takes
  only tracked files, and the form is tracked from its first captured save, so a hand edit
  reaches the milestone without the view's help.
- **One Save.** The form and the pipeline are one project on one ledger, so Save, Ctrl/Cmd+S
  and Commit save both, the same from either toolbar, and which view shows never decides
  what a save records. A form saved alone would leave the pipeline's copies behind it on
  disk until the next pipeline save, and a pipeline saved alone would carry copies of a
  form the view does not show; one save, the form before the pipeline with the nodes'
  copies following in between, records a pipeline that runs on the form it records. The
  form is saved only when there is something to save, unsaved edits or an uncaptured save,
  so a save from the pipeline editor costs the form no request and no toast while it is
  as saved.
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
- **A pause in typing.** The sample is priced 300ms after the last change rather than on
  each commit: the pause is the sign that a value is complete, a heuristic about the typist,
  not a rule about what an edit is, and undo never depends on it.
- **The form as it stands.** Pricing asks the server for the tables of the unsaved form
  rather than reading the saved file, so a Collection shows the pipeline's answer for what
  is on the sheet now, as a rater's cell would; the pipeline's own Workbench Input still
  follows the saved form, so a save is still what changes the pipeline.
- **One sheet, two sets of values.** The components draw the sample and the quote through
  one set of values given to the sheet, rather than a second body for Preview: what a cell
  holds, how it changes, what the last pricing gave and which cells are marked come from
  the form store while building and from the preview store in Preview, so a change to how
  a cell looks or a row is matched is made once.
- **Price on request.** The quote is priced when Price is pressed, not as it is typed: an
  underwriter fills a quote in before wanting its price, the check against the rules would
  otherwise mark every required cell at the first keystroke, and the pipeline is spared a
  pricing per pause. The sample, priced while building, keeps its pause.
- **Rows matched as the server typed them.** A Table's output rows are matched through
  the typed sample the pricing answers with, not the text in the grid: the server leaves
  empty rows out and numbers the index as the grid does, so the filled grid rows are its
  typed rows in order, and a key typed as "$1,000" matches an output row holding 1000.
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
- **Read as what they are.** A Quote Input's request is a JSON document, read through its
  tables' paths by the Quote Input's reader; a Workbench Input's is the workbench's tables,
  read as dataframes with no path, by a few rules a pricing analyst can hold in their head.
  The two share one decision, that either is the request input, and nothing else: the Quote
  Input's reader stays its own, the workbench's tables carry nothing derived, and a test
  reports a check that names the Quote Input alone where it means a request input.
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
- **The tables are the response.** A Workbench Output's response is its tables under their
  names, as a quote holds its tables, built where the pipeline answers a request and rendered
  as a Quote Response's is; nothing is placed at a path, so the response has exactly the
  tables' shape and deploy serves it unchanged.
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
  takes a Workbench Input's request schema and dry-run quote from its tables and reads a
  served request into them, and a deployed pipeline answers with a Workbench Output's
  response as with a Quote Response's.
- [git-integration](../git-integration/high-level.md): an unborn repository's seed commit
  takes `forms/` with the pipeline's files; a form save is one save on the clone's ledger
  through the pipeline save's capture, reported in the editor through the shared
  save-capture report, and a milestone's sweep takes the tracked form.
- [frontend-git-ui](../frontend-git-ui/high-level.md): the navigation guards (a move, a
  switch, an archive, a delete) ask about the form's unsaved edits through the workbench
  store's mirror of them, and the Git panel's save before a switch is the project's save.
- [sandbox-security](../sandbox-security/high-level.md): the local Host, Origin and session
  middleware gate the workbench routes.
- [frontend-shared](../frontend-shared/high-level.md): the API client validates the four
  workbench responses with the generated contract; the workbench's toolbar is built from
  the kit's brand, Undo/Redo and Zoom In/Zoom Out and the project's controls the pipeline
  toolbar ends with; its palette is the kit's palette shell, whose reveal strip takes a
  label; its properties panel is the kit's side panel; the schema editor's and the panel's
  fields are the shared form primitives (the committed and validated text fields, the
  checkbox and the icon select); and the fields' order uses the list-reorder hook the step
  editor's cards use.
- [engineering-quality](../engineering-quality/high-level.md): the generated contract bundle
  carries the `workbench` response group.
- [json-shredding](../json-shredding/high-level.md): the Quote Input's reader, which the
  Workbench Input leaves alone; its columns' types are the Quote Input's, held equal by a test,
  and a table's name follows the one rule a Quote Input's labels follow.
- [frontend-node-editors](../frontend-node-editors/high-level.md): the palette's Workbench
  Input and Workbench Output and their panels.
- [frontend-graph-canvas](../frontend-graph-canvas/high-level.md): the editor shell hosts
  the view, hiding and fencing the pipeline editor while it shows, hands it the whole
  document as it holds it for pricing the sample, and its keyboard shortcuts register
  nothing then; a palette drop reports the node it creates, the commit
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
  "stale_document_revision: <form> changed on disk after the workbench read it. Reload the
  workbench before saving." when the file's revision is not the `base_revision` quoted (a
  file that appeared since a first save included), writing nothing; 409 "<form> could not
  be written: <reason>" when the file or its folder cannot be written; and FastAPI's 422 for
  a body that is not a form, as any typed body answers. A sample cell that is not text or a
  tick (a number, a null) is not a form.
  Saves take turns under the pipeline save's lock: of two quoting one revision, the second
  finds the first's and is refused. The
  view reports a stale refusal in a toast and a banner, keeping the edits until Reload, and
  any other failed save in one error toast naming the cause. A save that lands but whose
  capture failed or was skipped answers 200 with the warning, as the pipeline's save does;
  the view toasts it, and asks for a git identity once per session when the capture waits
  on one. A file that could not be read again after the pipeline's document was adopted
  leaves the form as it is, said in one error toast.
- A sheet's components are deleted with it, after a confirmation; the last sheet cannot
  be removed, and the view never offers to. A layout value outside its range (a Table's
  rows 1 to 50, a Collection's columns 1 to 12) is refused at the field, which says the
  range. A component dropped outside the sheet's viewport is not added. Fit never takes
  the zoom below its minimum: a sheet too wide to fit at 25% fits at 25%.
- `POST /api/workbench/tables` answers 404 while the workbench is not enabled, the
  structured 422 for tables the Quote Input's rules refuse and FastAPI's 422 for a body
  that is not a form; it writes nothing. A pricing that fails for any reason, a pipeline
  without a Workbench Output, a table nothing is connected to, a sample that does not fit
  its tables or the preview route's failure, shows as the toolbar's reason and empties the
  output columns, never as a toast; an answer that arrives after the view has left is
  dropped.
- A quote that breaks a column's rule is not priced: the cells are marked, the toolbar
  says how many need attention, and nothing is sent. A pricing of the quote that fails
  shows "Pricing failed: …" in the toolbar and empties the output columns, as the sample's
  does; a press of Price while one runs does nothing.
- The schema editor refuses nothing: a problem is shown beside its table, and a save
  writes the form as it is. The tables route then answers the structured 422 for a table
  the pipeline cannot take, which the tables fetch shows as one toast, so the problem is
  visible in both views until it is fixed.
- `GET /api/workbench/tables` answers 404 while the workbench is not enabled; the editor
  never asks then. Tables or response tables the pipeline cannot take, such as a table named
  with a Python keyword, two tables of one name or two that differ only in case, answer the
  structured 422 of a public contract error (`workbench_tables_invalid`), the message naming
  the table.
- A failed status request shows one error toast; the editor behaves as it does with the
  workbench not enabled.
- A Workbench Input or Workbench Output in a project whose workbench is not enabled keeps its
  copy and runs from it; its panel says so and nothing updates it.
- A Workbench Input with no tables has no ports, and nor has one whose tables have no
  columns yet. Previewing either fails with "This Workbench Input has no tables: add input
  tables to the workbench's schema and save it." or "This Workbench Input's tables have no
  columns yet: add their columns in the workbench's schema and save it.", and deploying it
  fails for the same reason. A copy that is not as the workbench writes it, such as one
  holding a key the workbench does not write, fails wherever it is read with "The Workbench
  Input's tables are not as the workbench writes them (<where>: <what>). Open the pipeline in
  the editor and save it.", the editor's fetch being what rewrites the copy.
- A sample that does not fit its Workbench Input's tables fails whatever reads its rows
  (previews, `run()`, leasing a table point), and planning a preview that sizes the tables,
  with "The workbench's sample does not fit this Workbench Input's tables: <reason>. Correct
  it in the workbench and save it." It does not fit when a value is not of its column's
  declared type (`policy.limit is 'lots', not 'int'`), when something other than a list
  stands where a many-row table's rows belong, when something other than an object stands
  where a one-row table's belongs, or when a many-row table's list holds anything but rows (a
  value, `null` or a list). Resolving a table point and the cache report read the tables
  alone, so a misfit sample never stops them.
- A deployed pipeline's request of other than one quote fails with "A Workbench Input reads
  one quote per request, and this request holds <n>.", and a quote that does not fit the
  tables with "The request does not fit this Workbench Input's tables: <reason>.", the reasons
  the sample's are. Either answers `/quote` as 422 with the input's own code,
  `workbench_input_invalid`, a value Polars itself could not build a frame from included,
  since the request is read from its records.
- Two request inputs of either type, at the top level or in a submodel, fail save and deploy
  with "Only one Quote Input or Workbench Input node is allowed per pipeline (found 2)." and
  `Pipeline.score` as two Quote Inputs do. A config key a Workbench Input does not declare,
  `path` among them, is refused as it is for any node type.
- Running a Workbench Output, in a preview, `run()`, `score()` or a deployed pipeline, fails
  with a message naming its table:
  - with no tables, "This Workbench Output has no tables: add output tables to the
    workbench's schema and save it.", and with tables that have no columns yet, "This
    Workbench Output's tables have no columns yet: add their columns in the workbench's
    schema and save it.";
  - with a copy that is not as the workbench writes it, as a Workbench Input's fails;
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
