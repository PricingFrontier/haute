# Workbench — High-Level Specification

## Purpose

The workbench is where a pricing team lays out the quote an underwriter keys in: a schema
of typed tables, the sheets, each a stack of sections holding Tables and Collections that
show those tables' columns, Charts that plot them and Headings that name them, and the
sample quote typed into them in Preview. It is part of Haute, switched on per
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
its sheets, drawn while building as an underwriter's new quote opens, and Preview, the
sheets as an underwriter uses them, with the sample typed into them and priced live), and
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
- `GET /api/workbench/params`, a params file's header and a column's distinct values,
  which a column's allowed values are read from.
- The Workbench view: how it is shown beside the pipeline editor and what the pipeline
  editor does meanwhile, its toolbar and keyboard shortcuts, its schema editor, its sheets
  (the canvas and its sections, the Table, the Collection, the Chart and the Heading, their
  palette and properties panel, and the sheet tabs), drawn while building as a new quote,
  Preview, where the sample is keyed into the sheets as an underwriter keys a quote and
  priced live on the pipeline, and how the form is read into it and saved from it.
- The Workbench Input, the node type that holds a copy of the tables and sample, which the
  editor keeps current and shows, and previews on.
- The Workbench Output, the node type that holds a copy of the output tables, which the
  editor keeps current and shows, and whose ports take the frames that fill them.
- The quote store: saved quotes and their revisions, each with the form it was keyed
  into, on a SQLite file, its schema and the migrations that bring a database to it.
- The test quotes an analyst keeps in Preview, in the project's test-quote file, and the
  routes that save, list, open, compare, accept and delete them.

Out of scope:

- The toolbar's pipeline controls ([frontend-shared](../frontend-shared/high-level.md)) and
  the canvas, palette and keyboard shortcuts
  ([frontend-graph-canvas](../frontend-graph-canvas/high-level.md)).
- The Host, Origin and session-cookie checks
  ([sandbox-security](../sandbox-security/high-level.md)), which apply to the workbench
  routes exactly as to any other `/api/` route.
- How a deployed pipeline reads a request into its tables ([deploy](../deploy/high-level.md)).
- The underwriter app on hosts other than Azure Container Apps
  ([WB-07](../roadmap/workbench.md#wb-07--the-underwriter-app-on-other-hosts)).
- The underwriter app's host-side delivery: rolling a host to a release and checking it
  there ([WB-08](../roadmap/workbench.md#wb-08--the-workbench-projects-cicd)).
- Where a deployment's quote store is configured and migrated, the server databases it
  will run on, and the app's Save, Open and list of quotes
  ([WB-10](../roadmap/workbench.md#wb-10--the-quote-store)).
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
  `haute.toml` accepts the table with those two keys, and while `enabled` is true deploy
  holds each Workbench node's copy of the tables to the form, read strictly, naming each
  table or column that differs ([deploy](../deploy/high-level.md)).
  `haute init --workbench` writes the table and a blank form, and the deploy workflow it
  generates for GitHub or Azure DevOps also starts when a file under `forms/` changes;
  `haute init --force` rewrites `haute.toml`, so the table stays only when `--workbench`
  is given again, while `forms/` is left as `data/` is. An existing project enables its
  workbench by adding the table by hand. A nested `[workbench.app]` table says where the
  underwriter app runs: `host` is `azure-container-apps`, and `edit_role`, when given, is
  the role the host signs underwriters in with that may change quotes, every other
  underwriter reading them; any other key, host or role that is not a name is refused by
  name.
- **The form file.** `forms/form.json` is one JSON document: `version` (1), `name`, `schema`
  (its tables), `pages` (its sheets, at least one) and `sample` (the quote typed while
  building, `{}` for none). Every field has one canonical spelling and an unknown field is
  refused. A schema table has an id, a name, a `role` (`input`, sent to the pricing engine,
  or `output`, returned by it), `rows` (`one` per quote, such as policy details, or `many`,
  such as an equipment schedule) and its columns, each with an id, a name, a `type` of the
  Quote Input's column types (`str`, `int`, `float`, `bool`, `date`), a `label`, its
  `group` (a name it shares with the columns grouped with it, `""` for none, which a
  Table showing groups draws over its labels), whether it
  is a `key` (a many-row table's keys say which row is which), its rules (`required`, `min`,
  `max`, `options`, and `options_source`, the `file` in the pipeline folder's
  `files/params/` and the `column` the allowed values were read from, `null` for none,
  or for values typed in, which only an older form holds), whether it gives its table a
  row for each of its allowed values (`row_per_value`, below), whether it is the table's
  `index`, the column numbering its rows
  from 1, and how the sheets show it: its `help`, shown beside its label, its
  `placeholder`, what an empty input cell shows, its `default_value`, what a new quote
  starts with in it (text as typed, or `true` for a tick, `""` for none; only a one-row
  input table's columns start a new quote with theirs), and a number's format as an Explore
  pivot's values take one, `number_format` (`general`, `number`, `percent`,
  `currency_gbp`, `currency_usd`, `currency_eur`), `decimal_places` (0 to 10, or null for
  automatic) and `use_grouping`. Every schema holds Haute's own table of a quote's
  details, `quote_details`, first: one row, an input, its columns `reference`,
  `quote_name`, `insured`, `broker`, `inception_date`, `expiry_date`, `valid_until`,
  `currency`, `stage`, `assignee`, `archived` and `is_renewal`, in that order, the three
  dates Dates, `archived` and `is_renewal` True/false and the rest Text, each with the id
  `qd_` and its name. A form read or saved without it takes it on, and one holding its
  columns as an earlier Haute made them (the first eleven, before `is_renewal`, or more of
  them in order) takes the newer ones on after them; one whose `quote_details` is not Haute's (the
  name on another table, or its role, rows, columns, keys or index changed) is refused,
  as is a rule or a default on `reference`, `stage`, `assignee` or `archived`, which the
  underwriter app sets. Its labels, groups, help and placeholders are the project's, as are
  the rules and defaults of the columns the underwriter types; the sheets show its columns, and the pipeline receives
  it, as any other input table's. A sheet has an id, a title, its `width` in pixels, when it
  shows (`show_when`) and its sections (`sections`, at least one): strips across the sheet's
  whole width, stacked in
  their order, each with an id, a `title`, its `height` in pixels, when it shows
  (`show_when`) and its components (`widgets` in the file): a Table (`tableInput`, a grid of at
  least `rows` rows showing columns of many-row tables, with its look, whether its rows
  are fixed and whether it is transposed: `row_numbers`, `density` (`compact` or
  `comfortable`), `gridlines` (`rows`, `all` or `none`), `striped`, `fixed_rows`,
  `transposed` and `groups`), a Collection (`collection`, boxes
  showing columns of one-row tables, `columns` across, and its `spacers`, the gaps among
  them, each the number of fields before it, in order and none past the last), a Chart (`chart`, plotting number
  columns, with what labels the rows of its many-row tables, `category`, a schema column
  named as a field is or null to number them, and how it draws: `chart_type` (`column`,
  `line`, `area` or `pie`), `orientation` (`vertical` or `horizontal`), `stacked`, `legend`
  (`bottom`, `top`, `left`, `right` or `none`) and `data_labels`) or a Heading (`heading`,
  its `text`, naming a section of the sheet, the `description` under it, its `size`,
  `large`, `medium` or `small`, and its `align`, `left`, `center` or `right`),
  each placed in its section by its `x`, `y`, `w`, `h`, measured from the section's top-left
  corner and inside it, its `y` plus its `h` at most the section's `height` and its `x` plus
  its `w` at most the sheet's `width`, and with a
  `show_when` of its own, and each but a
  Heading with a `title` and showing its `fields`, schema columns named by table id and
  column id so a rename keeps them. A `show_when` holds `conditions` and whether `all`
  of them must hold or `any` (`match`); with none, the sheet, section or component always shows. A
  condition names a column by table id and column id, a test (`op`) and the test's `values`,
  as text: `ticked` or `not_ticked`, with no value, for a True/false column; `filled` or
  `empty`, with no value, and `equals` or `not_equals`, with one, for any other; `one_of` or
  `not_one_of`, with one or more, for a Text or number column; and `above`, `at_least`,
  `below` or `at_most`, with one, for a number or a Date column. The sample holds, by table
  id, rows keyed by column id, each value as typed: text or a tick. Haute reads the file whole whenever it needs it and never keeps it in memory, so an
  edit made outside the view reaches the next request. A file that does not exist is the
  blank form, never saved: one sheet 1280 wide holding one section 560 tall, no tables, no
  sample. A
  sheet holding `widgets` of its own, as sheets did before sections, is refused by name
  (Failure model).
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
- **Params files.** `GET /api/workbench/params` with a `file` answers the header of that
  CSV in the pipeline folder's `files/params/` and, given a `column`, the column's
  distinct values: each as written, every value text, read as UTF-8 or else
  Windows-1252 (as Excel saves a CSV on Windows), trimmed, blanks left out, in the order
  the file first gives them. It writes nothing.
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
    the view never edits the hidden pipeline. The Git, Assistant and Files panels open
    beside the view, in the properties panel's place. The Workbench Input's and Workbench
    Output's panels offer "Edit in Workbench", which shows the view.
  - **The toolbar** keeps the brand and the project's controls (Pipeline files and Local
    files, Assistant, Help, the branch, Save and Commit) and shows the view's own, by mode:

    | Mode | The view's controls |
    |---|---|
    | Building or Preview | Build over Preview |
    | Building, the sheets showing | Sheets over Schema, Display, the form's Undo and Redo, and Zoom In over Zoom Out |
    | Building, the schema showing | Sheets over Schema, Display, and Undo and Redo |
    | Preview | Test quotes, the form's Undo and Redo, Zoom In over Zoom Out, and why pricing the sample last failed; no Price and no Clear |

  - **Save.** Save and Ctrl/Cmd+S save the project, the same from either toolbar, with a
    focused field's edit included: the form is saved first when it holds unsaved edits or
    a save the ledger did not capture (for want of an identity, or because the capture
    failed), the Workbench Input's and Workbench Output's copies follow it, and then the
    pipeline is saved, so what is saved runs on the form that was saved. The tables are
    fetched afresh for that, and a fetch that fails refuses the pipeline's save, with a
    toast saying the pipeline was not saved, rather than record copies behind the form. A
    form save refused, as stale or failed, saves no pipeline, so the pipeline never carries
    copies of a form the view does not show; and a save of a pipeline document that cannot
    be saved (read-only, or changed on disk under unsaved edits), one asked for inside a
    submodel, or one asked for while the assistant's turn runs, is refused before the form
    is saved, with the pipeline save's own reason, so the form is never saved ahead of a
    pipeline that is not: the fence that disables Save in both toolbars holds for
    Ctrl/Cmd+S and the git flows too, and is read again after each wait, so a submodel
    opened, the assistant's turn started or the document changed while the form was being
    saved or the tables fetched refuses the rest of the save, the pipeline unsaved until
    the next Save; an update of a node the document refuses outright is reported as
    copies not brought up to date, never as a pipeline ready to save. A Save pressed
    while one runs waits for it and then
    saves the project as it stands, as the form's own saves queue behind one another; two
    running at once would each fetch the tables, and the second fetch would supersede the
    first's updates of the nodes, which a save counts as a failure. Save passes the pipeline
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
  from it, its name and, in a many-row table, whether it is a key. A column's label, group
  and help open beside it, the group offered from those its table's columns have, the boxes
  holding no hint text; for an Integer or a Decimal other than the index, its format, decimal
  places and thousands separators, separators ticked under General making it a Number as
  an Explore pivot does; and, in an input table, its default in a one-row table (ticked or
  not for True/false, one of the allowed values while it has some, a date for a Date, else
  typed), its placeholder for any type but
  True/false and Date and its rules: required, a range for an Integer or a Decimal, and the
  allowed values for any type but True/false and Date, never typed in but read from a
  CSV in the pipeline folder's `files/params/`: its file chosen, then its column from the
  file's header, and the column's distinct values, as written, trimmed, blanks left out
  and in the order the file first gives them, copied in with where they came from as one
  edit; the refresh button beside them reads them again, an edit only when they changed,
  and the blank file takes them away, values typed in as an older form holds them
  included, which the choice shows as typed in. A read answers only while the column's
  source is still the one it began from, so one an undo overtakes changes nothing. What
  stops a file or a column giving them shows under them. Changing a
  column's type drops the rules and the format the new type cannot have, and its default
  when it becomes or stops being True/false, or becomes a Date while it is not a day.
  Every text field
  commits on blur or Enter, so an edit is one undo step; Enter in a column's name adds the
  next column, Alt+Up and Alt+Down move one, and a new table's or column's name takes the
  focus. A many-row table can have an index: a column first in the table, `row_number`
  until renamed, an Integer whose type cannot change, numbering the rows from 1, which
  Alt+Up and Alt+Down neither move nor pass; switching a table to one row clears its keys
  and drops its index, with what shows it, and switching it to many rows clears its
  columns' defaults. Removing a column or a table a sheet shows, or a
  sheet's, a section's or a component's condition tests, asks first, and takes it off the
  sheet, out of those conditions and out of the sample. The editor says what would stop
  the schema being the pipeline's tables: a table's name is held to the port rules the
  Quote Input's labels are held to (an identifier, none of the document's reserved labels,
  unique whatever its case), a column's name is an identifier that is not a Python keyword,
  as the server holds it, unique in its table, a many-row table needs a key, an input
  column's range must not be inverted and its allowed values must be of its type and
  read from a params file, not typed in, as an older form may hold them, True/false and
  Date columns can have none, and a default must be a value its column
  takes: a tick for True/false, else text of its type, within its range and one of its
  allowed values. Haute's `quote_details` table, first, keeps
  its structure: its name, role and rows and its columns' names and types are fixed, and
  it is neither deleted nor given a column; its columns take labels, groups and help, and
  those the underwriter types their defaults, placeholders and rules, while `reference`,
  `stage`, `assignee` and `archived`, which the app sets, take a label, a group and help
  alone.
- **The sheets.** A sheet is a canvas on an 8px snap grid as wide as its width and as
  long as its sections, drawn at the zoom from its viewport's top-left corner, so it
  stays beside the palette and under the tabs: zooming out draws it smaller, the canvas
  to its right and below it, and it is scrolled across while wider than the viewport. Its tabs run along the top: the sheet showing is
  marked, a plus adds a sheet named in turn, double-click renames one, a tab dragged onto
  another moves its sheet there, as Alt+Left and Alt+Right move the focused tab's sheet a
  place, the tab keeping the focus, each move one undo step and the sheet showing still
  the one that shows, and the showing sheet's own panel
  deletes it (the properties panel, below), while another sheet remains; the
  schema and the sample are untouched. A sheet is a stack of its sections, each a strip
  across its whole width as tall as its height, one under the next in their order, the
  sheet ending where the last one ends, so a taller section is a longer sheet; a new
  sheet is as wide as the last and holds one section, 560 tall, so the whole sheet is one
  section until another is added. A
  section is added from the palette (below) like a component, and drawn on the sheet:
  while building, each section sits under a bar 24 tall across the sheet that names it,
  with a grip, its title, an eye while it has conditions (Shown when, below) and a mark in
  the warning colour while it has a problem, so the sheet is 24 taller per section while
  building than in Preview. Pressing a bar, or Enter or Space on it, selects its section
  and opens the section's panel, the section outlined in the accent colour; a bar dragged
  up or down past others moves its section there, a line marking where it would go, as
  Alt+Up and Alt+Down move the focused bar's section a place, the bar keeping the focus;
  the selected section has a handle under the middle of its bottom whose drag sets its
  height, on the grid, never less than its components reach nor than 48, and a
  double-click on it fits the section, 16 below its lowest component, or 48 with none.
  Each is one undo step. A component stays inside its section: dragged, nudged, dropped
  or copied, it stops at the section's top and bottom, never lying across a border, and
  moves into another section only as its top edge goes over that one; resized past the
  bottom, it grows the section, pushing the sections under it down, as does a component
  dropped or moved into a section shorter than itself. A section never shrinks of its own
  accord. So a component stays inside the sheet's width: dragged, nudged, dropped or
  copied, it stops at the sheet's left and right edges, one dropped beside the sheet
  landing at the nearer; resized past the right edge, it widens the sheet, as does
  a component dropped onto a sheet narrower than itself, and the sheet never narrows of
  its own accord. A component is drawn in its
  section where the form places it: a Table as a grid of its rows with a column per field it shows, the index
  column numbering the rows, under a header of its labels, as written unless `header` is
  `caps`, in capitals, that stays in view as
  the rows scroll and is ruled off from them whatever the gridlines, drawn as its options
  say: its rows numbered in a column of their own unless `row_numbers` is off, compact rows
  (28px) or comfortable ones (36px), a line between rows and before each output column
  (`rows`), between every column too (`all`) or none at all (`none`), every other row
  shaded unless `striped` is off, and Add row under the last row and a delete on each row
  unless its rows are fixed; transposed (`transposed`), the same grid on its side: a line
  per field, labelled at its start in the header's look, the labels staying in view as
  the rows scroll across and ruled off from them, and a column per row, numbered above
  unless `row_numbers` is off, the numbers staying in view as the fields scroll down,
  with a delete over each and an add after the last unless its rows are fixed; its
  stripes and gridlines are its lines' as drawn, a full grid lining its columns too, and
  an output field is ruled off above its line; showing groups (`groups`), while a field
  it shows is in a schema group, each run of fields side by side in one group headed
  by one cell naming it, a row of them over the labels with each name ruled under
  inside its cell, so groups side by side stay apart, and a blank cell over a field in
  none, the numbers and the deletes, or, transposed, a column of them before the
  labels, which stay in view beside it; a Collection as boxes, its columns across, their labels above
  them or, while `labels` is `beside`, beside them, a tick box in a box as the other fields
  are, the boxes compact (28px) or comfortable (36px) as its `density` says, and each
  spacer a gap across its whole width where it sits among the fields, the fields after it
  starting a new row below the gap; each of the two in a frame, a border round the panel's
  colour, unless `frame` is off, when it sits on the sheet as its title and fields alone,
  all in the sheets' colours (below);
  a Chart as its type draws the values it plots (Charts, below); a Heading as its
  text (Headings, below). Each field of a
  Table or a Collection shows its label (its column's, or the name made readable), a star for
  a required input, a mark showing its help when it has some, a hint at its type (a
  dropdown's chevron, a date's calendar), an empty cell blank or showing its column's
  placeholder, a number in its column's format (a percent the value times 100, so a
  fraction typed into an input, 0.125, shows as 12.5%; an input as typed while it is typed
  into), an output column shaded, and "Missing column" when its
  column is gone.
  Dragging a component moves it, into the section its top edge is over (the first while
  above them all, the last while below them all), kept inside that section; selected, it
  has eight handles that resize it, no smaller than its kind's minimum and never above its
  section's top, a resize past its bottom growing the section. Positions snap to the grid, pointer movement is
  divided by the zoom, and a move or a resize is one undo step, a click that does not
  move, or a drag held at a limit or brought back where it began, recording nothing, so
  redo still stands. A field in the panel typed into and not yet committed commits, as an
  undo step of its own, as any drag on the sheet or out of the palette starts, and as the
  empty sheet is pressed. Pressing the empty sheet deselects. A component takes the
  keyboard's focus too: reached by Tab, it is selected, so the sheet's keys work without a
  pointer. The components are read, and reached by Tab, in the order they sit on the sheet,
  section by section and, within a section, row by row from the top and left to right along
  each row, whatever order they were added
  in: a row is the topmost component not yet read and every one beside it, starting above
  its middle, so a Heading placed above a section is read before it and a label a little
  lower than the box beside it is read with it. Where two overlap, the one added later is
  drawn over the other (a copy just over its original) and the selected one over both. A component with a problem (no fields, a Heading with no text, a column no longer in the schema, a table
  of the other kind, tables whose rows do not line up, a Chart's field that is not a number
  or is an index, a pie of many rows with more than one field, or one of its conditions',
  Shown when below) gets a dashed frame in the warning colour naming the problem, on hover and to
  a screen reader. One with no fields asks for them, while building, from the panel on
  the right; in Preview, which has no panel, it is its frame alone, as a Heading with no
  text is. The zoom runs from 25% to
  200% in steps of 10%; the sheet showing is fitted across the viewport, never past 100%,
  when the sheets first show for a form read (a zoom chosen since is kept across a trip to
  the schema or the pipeline editor) and on Ctrl/Cmd+1.
- **The palette.** The view's left column is Haute's palette shell with a Section and a
  Heading, in the structure colour, a Collection and a Table, in the entry colour, a Chart,
  in Explore's colour, and the switcher under them. A Section dragged onto the sheet
  shows a line across it where the new section would start, and released there starts it:
  inside a section, at the grid line nearest the pointer, moved to the nearer edge of any
  component the line would cut through, so it never runs through one, and never less than
  48px down the section, the least the section above can be (nearer its top than that,
  before the section or at the first clear line that far down, whichever is nearer the
  pointer), so the line drawn is where the section starts, it splits that
  section there, the components below the line moving into the new one and keeping their
  places on the sheet, the section above keeping the height above the line; on a
  section's bar or at its very top it goes in before it, at its very bottom after it, and
  below the last section at the end, empty and 160 tall. Either way it is named in turn,
  "Section N", always shown, selected and one undo step. A component is dragged out of it onto the
  sheet: a chip follows the pointer until the sheet shows a ghost where the component would
  land; released there, it is added to the section the ghost's top edge is over, as a
  dragged component moves (a Table 720 by 200 with 3 rows, numbered, compact,
  lined between rows, striped and not fixed, a Collection 720 by 120 with 3 columns
  across, a Chart 480 by 280 of columns, upright, unstacked, its rows numbered, its legend
  at the bottom and no data labels, a Heading 480 by 48 of medium size, aligned left),
  showing nothing yet, and selected.
  A click adds nothing. The palette shares the node palette's open state, so collapsing it
  in either view collapses both; collapsed, it is the reveal strip with the compact
  switcher under it.
- **The properties panel.** Selecting a component opens its panel on the right, Haute's
  side panel resized from its left edge, where the Git and Assistant panels open while
  they are closed: the component's kind and its title; its Show when (below); once it shows a field, its layout,
  the rows a Table's grid starts with (1 to 50), whether they are fixed and whether it is
  transposed, or a Collection's columns across (1
  to 12), a value outside the range refused at the field, a Table's look (its density, its
  gridlines, its header, Capitals or As written, its row numbers, its groups, its
  stripes and its frame), a Collection's look (its labels, Above or Beside, its density and its frame), a
  Chart's chart (its type, Column, Line,
  Area or Pie; while it plots many-row tables, what labels its rows, "Row number" or a
  column of a table whose rows line up with its fields, other than an index, by table and
  label; its legend, Bottom, Top, Left, Right or None; and whether it is stacked, horizontal
  and shows its values as data labels, a pie being neither stacked nor horizontal), and
  the order of the fields it shows, moved
  by dragging or Alt+Up and Alt+Down, each with a cross that stops showing it, and in a
  Collection's its spacers among them, each a row moved as a field is, with a cross that
  removes it, and Add spacer under them adding one after the last field; and its
  fields, ticked from
  the schema tables of its
  kind, many-row tables for a Table, one-row tables for a Collection and any table for a
  Chart, each table
  collapsible with its role and how many of its columns show, each column with its type's
  icon, its name and its label, a Chart's columns other than numbers and an index disabled,
  since a Chart plots numbers. Fields are held by table and column id, in the order
  ticked, so a rename keeps them. A Collection's spacers keep their places among its
  fields: a field it stops showing, unticked, crossed out or gone from the schema, takes
  its place in the count with it, a field ticked goes after any spacer at the end, and
  one left showing no fields keeps no spacers.
  A grid holds one kind of row, so once a Table shows a
  field, tables whose rows do not line up with it (keyed by other columns, or keyless) are
  greyed out with their unticked columns disabled; an input table and an output table
  keyed alike line up. A Chart's fields and the column labelling its rows line up the
  same way, the first of them deciding, and one-row tables all line up with one another.
  With no table of the component's kind in the schema, the panel says so and offers the
  schema. A Table's and a Collection's panel puts these in two panes under its header, as
  Haute's node panel puts an Explore node's in panes: **Config**, the data it shows, its
  order and its fields, and **Display**, how it shows them, its title, its Show when, its
  layout and its look; the tabs are drawn in the component's colour, Config first,
  and the pane chosen for a component is kept for it while the editor is open. A Chart's
  panel stays one list. A Heading's panel has its text in the title's place and its description under
  it, each committed on blur or Enter, its Show when, and its look: its size, Large, Medium
  or Small, and its alignment, Left, Centre or Right, one of three toggles the arrow keys
  also move between. While building, the showing sheet's tab has a settings
  mark, and no delete of its own, that opens the sheet's own panel in the same place, with its
  title, its Show when and its layout, its width in whole pixels (no less than 240 or
  than its components reach, a value below that refused at the field), and at its foot
  Delete sheet, disabled while it is the only sheet, which asks first in a dialog of the
  editor's own, never the browser's: it names the sheet and how many components go with
  it, the schema and the sample staying; Cancel, Escape or a press outside it keeps the
  sheet, and Delete deletes it as one undo step, the first sheet left showing and its
  panel closed. A section's
  bar opens the section's panel there: its
  title, committed on blur or Enter (a blank one kept out), its Show when, and its layout,
  its height in whole pixels (no less than 48 or than its components reach, a value below
  that refused at the field) with Fit beside it, Add section below, adding a section 160 tall
  under it, selected, and Delete section, asking first when components are in it, disabled
  while it is the sheet's only section. Pressing the empty sheet, Escape, the panel's close,
  or selecting a component or another section closes either. Show when reads "Always" while there are no
  conditions, and Add condition adds one on the first column of a one-row input table,
  with the first test its type takes and no value yet; each condition is a row of its
  column (a select of the one-row input tables' columns, by table, each by its label), its
  test (for True/false "is ticked" and "is not ticked"; for Text "equals", "does not
  equal", "is one of", "is not one of", "is filled" and "is empty"; for a number those and
  "is greater than", "is at least", "is less than" and "is at most"; for a Date "is on",
  "is not on", "is after", "is on or after", "is before", "is on or before", "is filled"
  and "is empty"), its value, the control the column's cell is (a dropdown of the allowed
  values, a date picker, else a text field, committing on blur or Enter), or for "is one
  of" and "is not one of" a tick box per allowed value, or the values typed comma-separated
  when the column allows any, and a cross that removes it. Choosing another column keeps
  the test while the column's type takes it, else the type's first, and the values while
  they fit the test; with two or more conditions the section reads "Show when [all|any] of
  these are true". Each change is one undo step.
- **The sheet's keyboard.** While the sheets show and a component is selected: Escape
  deselects it, Delete or Backspace removes it, Ctrl/Cmd+D duplicates it (16px below and
  to the right, selected), Ctrl/Cmd+C copies it, a note saying so, and the arrow keys nudge it by a grid step, or a pixel with
  Shift, a burst of nudges within 800ms being one undo step, kept inside its section, a
  nudge that cannot move it recording nothing. While the sheets show, selected or not,
  Ctrl/Cmd+V pastes a copy of the component copied, as it was when copied but for the
  columns the schema has lost since, which it no longer shows or tests, onto the sheet
  showing: into the section selected, else the selected component's, else the section it
  was copied from while it is on that sheet, else the sheet's first; where it sat, moved
  16px down and to the right while a component there sits at exactly that place, kept
  inside the section and the sheet's width, a new component, selected and one undo step.
  The copy is held while the editor is open, neither saved nor undone. With a section's bar focused: Enter or Space
  opens its panel, Alt+Up and Alt+Down move the section, and Delete or Backspace deletes it,
  asking first when components are in it, while another section remains on the sheet; with
  the section's panel open, Escape closes it. None of that from a text
  field or a select, whose own keys pick an option. Delete, Backspace, Ctrl/Cmd+D,
  Ctrl/Cmd+C and the arrows act on the selected component only from the sheet, or with
  nothing focused, as after a press on a component; from the properties panel, the
  toolbar or a tab they are the focused element's own.
- **The sheets' colours.** While building, Display in the toolbar opens a box under it,
  Haute's toolbar dropdown rather than a dialog so the sheet stays in view, holding the
  sheets' colours: the sheet's background, the components' background, their borders, the
  font, the input cells and the output cells, each Automatic (the editor's own), one of the
  swatches, white, light grey, dark grey and black and then Haute's eight hues, or a colour
  of its own from a square of a colour wheel's rainbow, chosen as an Explore chart's colour
  is; a colour
  of its own shows on the sheet at each step of its pick, and the whole pick is one undo
  step. Pressing outside the box, or Escape, closes it. The colours are the form's `theme`,
  each `#RRGGBB` in capitals or null for the editor's own, and colour the sheets wherever
  they show, the underwriter app's included: the sheet's background behind its sections, its
  dots and outline staying the editor's; the background of framed components, Tables,
  Collections and Charts, an unframed one sitting on the sheet; their borders, a frame's,
  the rule under a title, a Table's header rule, its gridlines and the line before an output
  column, and a Collection's boxes'; the font, the text of Headings, their descriptions too,
  and of components' titles, labels and values, an output's label too, while a column that
  is gone stays amber, a required input's star red and the index's numbers muted, and a
  Chart's text stays the chart's own; the fill of input cells and boxes; and that of output
  cells and boxes, in place of their shade. A Table's header strip takes the components'
  background while one is set, or the sheet's while the Table is unframed, else keeps its
  own; its stripes, a light shade, show whatever is under them.
- **A new quote while building.** While building, the sheets are drawn as an underwriter's
  new quote opens: every input cell empty but those a column's default fills, every output
  a dash, every Chart with nothing but defaults to chart, each grid showing the rows it
  starts with, and what shows decided by a quote holding its defaults alone (Shown when,
  below). The cells are inert, drawn as a new quote's
  rather than as a read-only quote's: they take no typing, and a press on one is a press
  on its component, which selects and moves it. Nothing is priced while building.
- **Defaults.** A new quote starts with each one-row input table's row holding its
  columns' defaults, as if typed: while building, and in the underwriter app's new quote.
  The sample keeps what was typed into it, and a quote made from another quote's revision
  its values. A default is not typing: a quote holding nothing else typed, the details the
  app sets aside, is neither marked nor priced nor saved, as a blank one is not, and a value
  typed equal to its column's default counts as the default. A many-row table's columns
  take no default.
- **A row for each value.** A column of a many-row input table that has allowed values can
  give the table a row for each of them, in their order (`row_per_value`), such as a
  coverage table with a row for each peril: the table then has those rows, each holding
  its value in that column, a new quote's included, and since its allowed values come from
  a params file, the rows follow a refresh and `haute app build`. The rows are read so
  wherever a table's values are read, on the sheets and in what the server prices, whatever
  the sample, a quote or a test quote holds: each value's row is the first holding it there,
  the rows holding nothing there fill the rows left in order, and any other row is left
  out. Each row is filled by its value, so once anything else in the quote is typed every
  one goes to the pipeline (while the sheets hide that column, its value is left out, as any
  hidden value is), a Chart plots each and a Table's output columns line up with each; but
  a row's value is not typing: a quote holding nothing else typed, a row left out included,
  is neither marked nor priced nor saved, and the server takes it as the empty quote, as it
  takes a blank one. A grid showing the table shows its rows, whatever its rows and fixed
  rows, with no Add row and no delete, the column's cells showing each row's value as text,
  which takes no typing, and a value typed into the grid writes all its rows. A grid
  showing it beside another many-row table whose rows are not the same (another column's
  values, or rows of its own) shows every row of each, a table's cells past its own rows
  taking no typing, and says its tables' rows do not line up. The first such column in
  a table gives its rows: ticking another unticks it, and a form holding two names the
  second among the schema's problems. A column of a one-row or an output table, the index
  and one without allowed values give none; taking a column's allowed values away,
  retyping it to True/false or a Date, or switching its table to one row unticks it, and
  unticked, its rows stay as values typed.
- **The sample.** Typing into a component's input columns in Preview types the form's
  sample: the quote an analyst keys in as an underwriter would, saved with the form and
  what the pipeline's previews run on (the Workbench Input, below). A Collection's boxes
  hold its one-row tables' one row; a Table's grid holds its
  many-row tables' rows, row by row, with Add row and a delete on each row, unless its rows
  are fixed or a column gives them (above), adding and deleting a row in every input table the grid shows at once, so
  their cells stay side by side; a one-row table's column in a grid shows that table's one
  value on every row. A grid starts with its rows: while its input tables hold no rows it
  shows that many empty ones, which the first value typed into it keeps; once they hold
  rows it shows every row they hold, so a row can be deleted below its rows, deleting the
  last starting it again, and a grid of fixed rows never shows fewer than its rows.
  Changing a Table's rows starts its grid in the sample again at them, so Preview shows
  the change: each of its input tables keeps its rows up to the last with a value typed,
  empty rows making up the rest, in the same undo step; nothing typed is dropped. Each
  cell is the control its column calls for: a tick box
  for True/false, a dropdown of the allowed values, a date picker, else a text field, a
  number's right-aligned. A text field commits on blur or Enter, a dropdown, a tick box and
  a date on change, each commit one undo step. Values are held as typed, text or a tick, by
  table id and column id; the server types them by their columns when the tables are made.
  Pressing a cell types there. The sample is part of the form: saved with it, undone like
  any edit, in Preview as while building, and dropped with a column or table removed from
  the schema.
- **Priced live.** While Preview shows, the sample is priced on the pipeline open in the
  editor whenever the schema, the sample or which of the sample's values are left out
  (Shown when, below) changes, 300ms after the last value entered (a text or number cell's
  when Enter or Tab is pressed or the cell is left, a tick, a choice or a date as it is
  made), and when Preview shows, as
  the pipeline may have changed meanwhile; laying the sheet out differently prices
  nothing, and nothing is priced while building. There is no Price and no Clear. The
  sample is priced whatever its cells hold, a value that breaks its column's rules
  included; with nothing typed in the cells the sheets show it is not priced, as a
  deployed request never holds a blank quote, and its outputs show dashes with nothing
  said. A pricing asks `POST /api/workbench/tables` for the tables
  and sample Haute takes from the form as it stands, then previews the Workbench Output a
  table at a time through the preview route on the document as the editor holds it, the
  whole pipeline even while a submodel is open, with the active source: the document's
  top-level Workbench Input and Workbench Output take the form's tables and sample for that
  request alone, as a fetch gives them, a workbench node inside a submodel keeps its copy,
  and the document itself is untouched. One pricing runs at a time; asked again meanwhile,
  it prices once more when it finishes, on the form as it is then. A Collection's output
  column shows its table's value in the answer in its column's format, or as the data
  preview formats a value while that format is General with automatic decimal places,
  dimmed while a newer answer is on its way, and a dash before the first answer, for
  a column nothing filled, and after pricing fails, when the toolbar says why ("Pricing
  failed: …") until a pricing succeeds. A Table's output column shows, on each grid row,
  the value of the row in the answer keyed like it: the output table's key columns hold
  what the server typed for the grid row's (the index numbered as the grid numbers the
  row), found through the first many-row input table the grid shows that is keyed alike;
  a grid row with nothing typed, or none keyed like it, shows a dash, and a price for other
  values is dimmed on every row.
- **Charts.** A Chart plots its fields' values as the sheets hold them, a new quote's
  defaults alone while building, and in Preview the sample or the underwriter app's
  quote, with what pricing them gave. An input column's value
  is read as the server reads it, a number typed with a currency sign or separators being
  that number; text that is not a number, a cell left blank and a value the sheets hide
  (Shown when, below) are gaps. An output column's value is the last answer's, nothing
  before the first answer and after a pricing fails, and the whole chart is dimmed while it
  plots a price for other values, or names its rows by one, as an output cell is. Of one-row tables, each field is a
  column, a point of the line or the area, or a slice of the pie, named by its label, in
  the fields' order. Of many-row tables, each field is a series over the rows: while it
  shows an input table, the rows typed into the input tables it shows, a row counting while
  something the quote takes is typed in it, in their order, and an output table's values
  those of the row in the answer keyed like each, as a Table's output columns are matched;
  showing only output tables, the rows the first one's answer holds, another's matched by
  key. Each row is named by its value in the column that labels the rows, as the sheets
  show it (a priced value for an output column), or by its number while none does; a pie of
  many rows is a slice per row of its first field. Each value is shown in its column's
  format, in the tooltip and as a data label, and the value axis takes the format its
  columns share, or a compact number when they differ. Columns, lines and areas stack while
  `stacked` is set and run across while `orientation` is `horizontal`, the first row at the
  top; the legend is drawn where `legend` says while there are two series or more, and for
  a pie. A Chart with nothing to plot says "Nothing to chart yet". A pie shares out a
  whole, so it draws no slices that would misstate its values: given a value below 0 it
  says so, naming the first and its value as its column shows it, and
  given nothing above 0, "Every value is 0, so there is no pie to draw"; a 0 or a gap
  beside others is no slice. While building, a Chart
  is laid out like any component; in Preview pointing at a column, a point or a slice names
  it and its value.
- **Headings.** A Heading is text placed on a sheet to name a section of it, or the sheet
  itself: large (20px) for a title, medium (16px) for a section's heading and small (12px,
  in the secondary text colour) for a label, each semibold, with its description, while it
  has one, under it in 12px regular type in the secondary text colour, to say what the
  section is for. It is drawn as its text alone, with no frame or background of its own,
  aligned left, centred or right as its `align` says, the description with it, wrapping
  within its width and centred in its height, and is a heading to a screen reader, its
  size giving the level. It holds no values: it shows no schema columns and takes nothing
  typed. While building, a Heading with no text says "Type its text in the panel on the
  right", its description not drawn; in Preview it shows no text, only the dashed frame
  any component with a problem has.
- **Preview.** The toolbar's Build over Preview switches the view between building and
  the sheets as an underwriter uses them: the same sheets, their sections that show stacked
  one under the next and each component where it sits in its section, read and
  tabbed through in the order they sit, no section bars, their
  tabs only switching sheets, the components neither selected nor moved, the palette's
  components disabled, no properties panel, and the zoom. The sheets are drawn as the
  underwriter app draws them, through the same components. The analyst keys the sample in
  as an underwriter keys a quote, through the same controls, and it is priced live
  (above), read as a request is, so it prices as it would deployed: every output column
  shows its value, a Collection's its table's one row and a Table's the row keyed like each
  grid row, and every Chart plots it. A value typed in a column since removed, in a second
  row of a table now with one row per quote, or in a column the sheets hide counts for
  nothing, as the server leaves it out of the quote. Once anything is typed, each cell that
  breaks its column's rules is outlined, naming its problem on hover: a required cell left
  empty, text that is not a number in a number column or not a whole number in a
  whole-number column, text that is not a day in a Date column, a number outside the
  column's range, a value that is not one of the
  allowed values; a one-row table's one row is always checked, a many-row table's filled
  rows only, a cell the sheets hide never, and a tick box and the index never fault. The
  marks never stop the pricing. Ctrl/Cmd+S still saves the project in Preview, Ctrl/Cmd+1
  fits the sheet, and Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z (or Ctrl/Cmd+Y) undo and redo
  outside a text field, as Undo and Redo do; the sheet's editing keys do nothing.
- **Test quotes.** An analyst keeps test quotes in Preview, in the project's test-quote
  file: `test_quotes.sqlite` in the test-quote directory (`[test_quotes] dir` of
  `haute.toml`, `tests/quotes` when absent), held by the quote store. The file and the
  project's `.gitattributes` are ordinary files of the project's own, as the Files panels
  take one: a path reached through a link or junction, or a file hard linked to another,
  is refused naming why, before anything is written. The toolbar's Test
  quotes, in Preview, opens a panel beside the sheets that lists them, the most recently
  saved first:
  - **Saved.** The quote on the sheets, the sample, under a name, with its values whatever
    its cells hold (one with nothing typed in the cells the sheets show is refused, "Type
    the quote first"), the request built from them as a pricing builds it (the cells the
    sheets hide left out), and the response its
    pricing gave as a deployed pipeline answers it: each output table under its name, a
    one-row table as an object of its columns and a many-row table as a list of them. A
    response that Preview cut short, past its row or cell limit, is refused naming the
    table and its rows, and so is a one-row table priced with other than one row. The form
    as the view holds it is kept with the quote.
  - **Opened.** A test quote's values replace the sample, one undo step, carried onto the
    form as the view holds it by the stable ids; each filled value with no place on it is
    named, by the names it was saved under.
  - **Run.** Run prices every test quote, or those ticked, one request each, on the
    pipeline, the data source and the form as they were when the run started, and compares
    each answer with the response the quote was accepted with: a number matches within one
    part in a billion and anything else exactly, walked into the response's tables, every
    difference listed by its place (`table.column`, `table[row].column`), the accepted
    value beside the new, a value on one side only as added or removed. Both responses
    are compared as a deployed pipeline answers, a null value and an empty table left out
    of each, so null and absent are alike. A quote with nothing typed in the cells the sheets
    show once it is carried onto the form is not priced and fails, as a save refuses one.
    Each quote shows
    unchanged, changed or failed with its reason, and a change to the pipeline, the source
    or the form after the run started marks the run's results stale.
  - **Accepted.** Accepting a changed quote saves its new response as a new revision,
    after the revision the run read; a quote saved since is refused, with nothing written,
    to be run again.
  - **Deleted.** Deleting a test quote, after a confirmation, removes it and its revisions.

  Every write to the file runs under the project's save lock and the repository's lock,
  which a commit, a branch switch and a merge hold, with the file opened for that write
  alone and closed before the lock is let go, and is captured on the save ledger as a save
  of the file, so a commit or a switch never meets the file half-written or open. A read
  holds the repository's lock alone. A file an older Haute wrote is brought to the newest
  schema by the next write and refused by a read until then, naming the save that brings
  it up to date; a project with no file lists no test quotes, and a read creates none.
  `haute init --workbench` writes the file empty, at the newest schema, so a milestone's
  sweep tracks it from the start, and the `.gitignore` guards keep SQLite's journal files
  out of the repository.

  In git the file is a binary, so each clone is given two drivers whenever Haute reads or
  writes its test quotes: `.gitattributes` names the file with
  `diff=haute-test-quotes merge=haute-test-quotes`, written and captured with the file, and
  the clone's own git configuration runs `haute test-quotes dump` to show the file as text
  in a diff (each quote by name: its revision, who saved it and when, its request and its
  response) and `haute test-quotes merge` to merge it, through the Python running Haute
  (`python -m haute.cli._test_quotes`, without the CLI's start-up, whose log line would
  join a diff's text).
  The merge takes the two sides quote by quote, by id, against their common base: a quote
  one side added, changed or deleted while the other left it as the base had it takes that
  side's; a quote changed on both sides, or changed on one and deleted on the other, is a
  conflict, named, the file left as it was on the branch being merged into. A provider's
  merge button runs no driver, so a pull request in conflict over the file is brought up
  to date where git runs it, on an analyst's machine (`git merge` of the target branch).
  Before Haute commits the file, it settles a rollback journal a write that never finished
  left beside it, as SQLite would on its next open: a hot journal is played back and
  removed, and one the write never synced is left, harmless, never committed. A file
  SQLite cannot open to settle refuses the commit, naming it.

  In CI, deploy's check reads the test quotes where the editor keeps them, `[test_quotes]
  dir` or `tests/quotes` when `haute.toml` names none, and builds each quote's request as
  a run does: its newest revision's values carried onto the form as the project holds it,
  each input column's allowed values read again from its params file as the app's release
  reads them. A value with no place on the form, a request that differs from the one the
  revision keeps (named by its place under `request.`), or nothing typed at all fails the
  quote. It then prices that request, one request each, and compares its answer with the
  response it was accepted with as a run compares it, so a change the panel shows is a
  change the check fails on; a test quote saved unpriced is priced with nothing to
  compare. `haute app build` checks against the form it ships, and the smoke test does
  the same against the deployed endpoint ([deploy](../deploy/high-level.md)). `haute test-quotes report` says what a
  branch does to the test quotes against its base: those added, removed and accepted
  anew, each with the outputs that moved; a workbench project's generated pull-request
  checks post it on the pull request on GitHub and keep it with the run on GitLab and
  Azure DevOps ([cli](../cli/high-level.md),
  [pipeline-config](../pipeline-config/high-level.md)).
- **The underwriter app.** The sheets reach underwriters through an app Haute builds from
  the project, apart from the builder. `haute app build` writes a release: the pipeline's
  bundle as a deploy builds it (the sample left out), the form without its sample
  (`form.json`), each input column's allowed values from a params file read again from
  the file as the project holds it, what the release is (`release.json`: an id taken from the form and the
  bundle, the form's revision, the pipeline's name, the Haute version that built it and
  when) and an entry point, `app.py`, serving it through the app Haute maintains.
  The release goes into a folder of its own: one that is or holds the project or the
  working directory, a file, or a folder holding anything but an earlier release is
  refused, and an earlier release is replaced only once the new one has built beside it,
  so a build that fails leaves it as it was.
  With `[deploy.container] base_image` set, pinned as a container deploy pins it, the
  release is also the image's build context, the container's Dockerfile copying it,
  and `haute app build --image TAG` builds the image with Docker; the image installs
  what a scoring container installs and the quote store's packages, SQLAlchemy and
  Alembic, pinned as the scoring runtime is, nothing more; a release whose Workbench
  Input copied the tables before the form held `quote_details` is refused as a deploy
  refuses it, asking for the project to be saved in the editor. The app
  serves the sheets on a page of their own, drawn as Preview draws them, with the same
  components filled from the app's routes rather than the builder's and nothing of the
  builder mounted: the sheets' tabs, the sheet showing, the release and who is signed in,
  and no Price and no Clear. A quote keyed in is marked against the columns' rules as
  Preview marks the sample, and priced as Preview prices it, 300ms after the last value
  entered,
  whatever its cells hold, a quote with nothing typed in the cells the sheets show, the
  details the app sets and its defaults aside, marked nowhere and priced not at all; the app prices it through
  `POST /api/app/price`, which takes the values and the form revision and release the page
  was loaded with. A page drawn for another
  revision or another release is refused (409), so a page loaded from an earlier release
  reloads rather than price on a newer contract or model, a release that changed the
  model alone included. The request is built as Preview's pricing builds it and priced through the deployed
  pipeline's engine, one quote per request, off the server's event loop, under the deploy's
  admission limit and a timeout, and answered whole: each output table's rows, with the
  request priced. `GET /api/app/release` and `GET /api/app/form` serve the release and the
  form; `GET /health` answers without sign-in, for the host's probes. Every other route
  needs the principal the host signed in. The identity follows `[workbench.app] host`:
  `azure-container-apps` reads the principal Azure's built-in authentication sets on each
  request, which the host strips from what a client sends, and refuses a request without
  it (401); a release built with no host refuses every request but the health check. A
  release with an `edit_role` reads the roles from the principal Azure passes whole
  (`X-MS-CLIENT-PRINCIPAL`, its claims of the type its `role_typ` names, an app role's
  when it names none): one holding the role may change quotes and any other reads them,
  `GET /api/app/release` saying which (`can_edit`), every change and save refused to a
  reader (403); a request whose roles cannot be read is refused (401), naming the header,
  since who may change quotes cannot be told. `haute app serve` builds a release of the
  project as it is saved and serves it on the analyst's machine, the principal the
  analyst's git identity, able to change quotes unless `--reader` signs them in as a
  reader, so the sheets underwriters will see are checked before a merge. No credential
  reaches the page.
  Given a quote store, the app keeps the quotes saved in it, each revision's
  `quote_details` row in a table of its own (the quote store, below). A hosted app reads the store's SQLAlchemy URL from
  `HAUTE_QUOTE_STORE_URL`, which the host supplies at runtime, and does not start on a
  store not migrated, which `haute app migrate` migrates before a release that needs it
  starts; `haute app serve` keeps its quotes in `.haute/app/quotes.sqlite`, beside the
  release rather than in it, migrated as it starts, so a rebuild keeps them. Without a
  store the app keeps nothing, says so on `GET /api/app/release` (`quotes`), and answers
  its quote routes 503. Its quote routes list a page of quotes
  (`GET /api/app/quotes`: the list, stage, search, assignee, order, cursor and size),
  create one (`POST /api/app/quotes`, made from another quote's revision when it names
  one), open one's newest revision or another (`GET /api/app/quotes/{id}` and
  `/api/app/quotes/{id}/revisions/{n}`), with the sheets that revision was keyed into when
  they are not the release's, carry a revision's values onto the release's sheets
  (`/api/app/quotes/{id}/revisions/{n}/carry`), save its next revision (`POST
  /api/app/quotes/{id}/revisions`), assign, note, archive, restore, mark final and
  reopen it (`POST /api/app/quotes/{id}/assignee`, `note`, `archive`, `restore`, `final`
  and `reopen`, each but the note answering with the revision it wrote), and read its
  history (`GET
  /api/app/quotes/{id}/history`) and the people quotes are with (`GET /api/app/people`).
  A save names the sheets' revision and the release it was drawn for, refused (409) for
  another, and an operation key; the details the app sets, `reference`, `stage`,
  `assignee` and `archived`, are set over whatever the page sent, and with nothing else
  typed there is nothing to save (422). It saves the quote unpriced, or, given the prices
  the page showed, prices it again on the server and keeps that pricing, saying whether
  it moved from what the page showed, so a price kept is always the release's own. Each
  change but a note names the quote's version and is refused (409) with the quote as it is now when
  the quote has moved on, or (409) naming what would let it when the quote does not take
  it as it stands; a quote the app does not keep is 404. A write whose browser says it
  came from another site, or, from a browser that does not say, whose origin is not the
  app's host, is refused (403). The
  app's pages, `/`, `/quotes/new`, `/quotes/{id}` and `/quotes/{id}/revisions/{n}`, are
  its one page, which reads its route in the browser.
- **The Quotes page.** With a quote store, the app opens on the Quotes page, at `/`. On
  the left are the lists, My work (the quotes assigned to the signed-in underwriter), Team
  (everyone's) and Archived, and the stages of the list showing, Draft, Priced and Final,
  each with its count; a stage picked narrows the list and picked again widens it. Above
  the list are its name, a search box (`/` focuses it) matching every word typed in the
  reference, quote name, insured or broker, in Team an assignee to narrow it to, the
  order (newest saved, newest reference, or insured) and New quote (`N`). The list is a
  table at Haute's density, 50 quotes at a time with Load more, read from the quotes'
  `quote_details`: each quote's reference, its title (its quote name, else its insured,
  else its reference), its insured, broker and inception date under their columns'
  labels on the sheets, the date as a day, its stage, who it is with and when it was saved,
  relative, with the time on hover. A row opens its quote, its reference a link of its
  own; on hover or focus it offers Open, Duplicate, and Archive or Restore. The list,
  stage, search, assignee and order are in the page's address, so Back returns to the
  list as it was. Loading shows placeholder rows, a failure says why with Retry, and an
  empty list says why: no quotes yet, with New quote; none matching, with Clear search;
  none archived.
- **A quote in the app.** A quote opens at `/quotes/{id}` on the sheets, under its bar:
  back to Quotes, its reference, its name (its `quote_details.quote_name`, typed in the
  bar as on the sheets and saved with the next Save), its stage and, when archived,
  Archived, who it is with (reassigned from the people quotes are with, or a name typed),
  Save, Mark final or, for a final quote, Reopen, which asks why, a menu of Duplicate,
  Renew, Create alternative, Archive or Restore and Copy link, History, and why the quote
  has no price. Beneath are its revision, who saved it and when, the release that priced
  it and the quote it came from. The quote is priced as it is typed, and as it opens while
  it may change and has no price of this release, unpriced or priced on an earlier one.
  Save waits for that pricing to settle, a value entered while it waits priced too, and
  keeps the quote priced when the values settle on a price, and unpriced when they could
  not be priced, which its label ("Save
  unpriced") and title say; with nothing typed,
  the details the app sets and the defaults aside, there is nothing to save, and nothing new to save while
  the values are as saved and priced as saved on this release (a price made since, or
  one of an earlier release, is new). A save and a change name the version the values
  were opened or saved at, which a note does not move, so a save over a quote a colleague
  moved on meanwhile is refused rather than written over theirs; an answer arriving once
  another quote is open, or the quote was left, is that quote's no longer and is dropped,
  and a quote being opened takes nothing typed and saves nothing until it is read. A
  question asked about a quote (Mark final, Reopen, a name for who it is with) is dropped
  as the page moves to another quote or is left, and a change only ever changes the quote
  it was asked about, refused naming it once another is open. A save is written once
  whatever is lost on the way: its operation key stays bound to what it sent, and a save
  whose answer never came, or came as a server failure (a status of 500 or more), is kept
  as it was sent, in this browser's draft too, so the next Save repeats it exactly before
  saving anything typed since as the quote's next revision; only a refusal (a status
  below 500) lets it go. While a save or a change is being made, Save and every control
  that changes the quote are off. The cells of `reference`,
  `stage`, `assignee` and `archived` take no typing; assigning,
  archiving, restoring, marking final and reopening each write a revision of the newest
  with those details changed, its values and prices kept, which the sheets take on, what
  was typed kept. A new quote, at `/quotes/new`, opens holding its columns' defaults and
  is saved by its first Save,
  which gives it its reference and its own address; one made from another quote's
  revision (Duplicate, Renew, Create alternative) opens with that revision's values
  carried onto the current sheets and priced afresh, naming what it was made from and any
  value that no longer fits; a renewal opens with `is_renewal` ticked, which the
  underwriter can untick, as one not made by Renew can be ticked by hand. The sheets take no typing and are not priced, and Save is
  off, while the quote is final, archived, shown at an earlier revision (at
  `/quotes/{id}/revisions/{n}`, with Open the newest) or saved on earlier sheets (opened
  on those sheets, with Move to current sheets, which carries its values onto the
  current ones as unsaved values naming any that no longer fits). A save the server
  priced differently from the sheets says so and shows the server's price. A save or
  change over a quote moved on since it was opened names who moved it, keeps the
  underwriter's values, and offers to open the newest or to save the values as a new
  quote; a page drawn for an earlier release says so and offers to reload. Values not
  yet saved are kept in the browser as they are typed, at once as the quote is left, and
  again after a save for what was typed during it, and offered back when the quote is
  opened again, or restored or reopened, as not saved to the server, until they are saved
  or discarded; only a quote that may change as it is shown offers them, and they are put back on its sheets'
  columns alone, the details the app sets as the quote has them. Leaving a quote with
  unsaved values asks first, Back and Forward too, the history's entries kept as they
  were, and closing the tab warns. Archiving says so with Undo, which restores the quote
  archived whichever is open by then. History, on the right, holds the handover note, edited there, the
  revisions, each opened as it was, what was done to the quote, by whom and when, and
  the quotes it came from or that were made from it. Ctrl+S saves.
  Without a quote store the app opens on a new quote, priced as it is typed, and says it
  keeps no quotes. One who may only read quotes is offered nothing that changes them: no
  New quote, Duplicate, Archive or Restore on the Quotes page, and every quote read only,
  saying that changing it needs the app's edit role.
- **Shown when.** A sheet, a section or a component with conditions shows only while they
  hold on the values typed into the sheets: a new quote's defaults alone while building, and in
  Preview the sample or the underwriter app's quote; a section on a sheet that does not
  show does not show either, nor does a
  component in a section that does not show.
  - **A condition holds** while its column is in a one-row input table and on a Table or a
    Collection that shows, and the value typed into it passes the test, read as the server types it:
    text without the spaces around it, compared exactly; a tick box ticked or not, one
    never touched unticked; a number read as the sample's numbers are (a currency sign,
    separators and spaces allowed) and compared as a number; a date as the day it names;
    and a cell blank or untouched as empty. Of the tests other than the ticks, an empty
    value passes `empty`, `not_equals` and `not_one_of` alone, and a value not of its
    column's type (text in a number column, or a tick left in a column since retyped)
    `filled`, `not_equals` and `not_one_of` alone. A condition that cannot hold as written
    never holds: one naming a column that is not in a one-row input table (gone, an output
    table's, a many-row table's), a test its column's type does not take, a number of
    values its test does not take (none for the ticks, `filled` and `empty`, at least one
    for `one_of` and `not_one_of`, else one), or a value its column could not hold (blank,
    not a number, a fraction for an Integer, not a `YYYY-MM-DD` day).
  - **Decided at once.** What shows is decided for the whole form together: everything
    starts shown and anything whose conditions fail is hidden, which may hide a column another
    condition tests, until nothing more hides. So a sheet that shows when a box on a
    hidden sheet is ticked is hidden too, whatever the box holds, as is a section that
    shows when a box in a hidden section is ticked, and the order of the sheets, sections
    and components decides nothing.
  - **A quote is what the sheets show.** A value typed into an input column that is on
    Tables or Collections, none of which shows, is left out of the quote, as if it had never
    been typed: a row with nothing typed in it but such values is left out as an empty row
    is; the index, which nothing is typed into, is never left out; and a column on no Table
    or Collection is not left out. A Chart takes nothing typed, so the columns it plots
    count for nothing here: a value only hidden components take is left out however a Chart
    that shows plots it, and a condition on a column only a Chart plots never holds. A
    Heading shows no columns, so it counts for nothing here either, while it hides and
    shows by its own conditions, its section's and its sheet's as any component does. The
    values are kept, not cleared: they stay in the sample and the
    quote as typed, the sample saved with them, and are in the quote again once their
    column shows. The server leaves them out where it makes a quote of the form's values,
    so the sample priced in Preview, the sample the Workbench Input's copy holds and the
    app's quote leave out the same values.
  - **While building** a new quote decides, its defaults alone typed: a hidden sheet's tab, a hidden section and a
    hidden component are drawn faded, still shown, selected and edited like any other, the
    sheet's sections keeping their places, and a sheet, a section or a component with
    conditions carries an eye (a section's in its bar), struck through while it is
    hidden, that names its conditions on hover.
  - **In Preview** the values decide as they are typed, the sample or the app's quote: a hidden sheet has no tab; a hidden
    section is not drawn and the sections under it move up to close the space it took; and
    a hidden component is not drawn, its place in its section left empty, since the
    components are placed freely in it. While the sheet chosen is hidden, the first sheet
    that shows is shown in its place, and while none shows the view says "No sheet shows
    for this quote." Fitting takes the components that show. While a pointer's primary
    button is pressed on the sheet, what shows and where, the sheet showing included, stays
    as it was when it was pressed, and changes once the press ends (released, cancelled, or
    taken by a context menu or another window), so a value committed by the press (a text
    cell left for a tick box) never moves the press's target from under it. When what shows
    changes above the section holding the focus, or else above the first section reaching
    into view, the sheet scrolls by as much as that section moved, so the underwriter's
    place stays where it was; nothing scrolls when that section itself closes.
  - **Problems.** The view names, on the dashed frame of a component, on a section's bar
    and in the warning colour on a sheet's tab, on hover and to a screen reader, what stops
    a sheet's, a section's or a component's conditions doing what they say: a condition
    that cannot hold as written, saying why; a value that is not one of its column's
    allowed values; and a sheet, section or component that nothing could show again once
    hidden, since each column its conditions need is on no Table or Collection, or only on
    itself, on its own components or on components that cannot show without it, as a tick
    box in the very section it shows is. None is refused: the form is saved as it is.
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
    null fails that dry run with its own error. Nothing deployed reads the sample, so the
    bundle leaves it out and carries the tables alone: the quote an analyst typed while
    building stays in the project. Deploy's test quotes are not held to the
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
    when there are several, the first table first. Clicking a value in them traces it from
    the node connected to its table: the same row of that node's frame, and the column that
    fills the value's column. A column nothing fills has no value to trace, and tracing it
    says so.
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
- **The quote store.** Quotes keyed into the sheets are saved to a store of Haute's, one
  schema for every database it runs on: a SQLite file now, for an analyst's machine and
  CI, and a server database the project's IT team provides for the underwriter app
  later. A quote has a stable id, a name, its owner, when it was made and numbered
  revisions. Each revision is written whole, in one transaction: the values as typed into
  the sheets, in the sample's shape (by table and column id, the cells the sheets hide
  kept); the request as it was priced; the response's tables in full, or none when it was
  saved unpriced; the release that priced it; who saved it and when; and the form it was
  keyed into, by its revision, the form's canonical text kept once for every quote that
  names it. A new revision names the one it follows and is refused, with nothing
  written, when the quote has moved on since. The owner is recorded and the list of
  quotes can be read for one owner. A quote the underwriter app keeps has, for each
  revision, that revision's `quote_details` row in a table of its own, a column each,
  which a test quote never has; the revision's values hold every other table, so the
  details are kept once. The row holds the details the underwriter typed and those the
  app sets: a reference, `Q-` and six digits, taken from a counter in the transaction
  that creates the quote, so two quotes made at once take two and one whose creation
  fails keeps none; its stage, `draft` or `priced` as the revision was saved, or
  `final`; who it is assigned to, at first who made it; and whether it is archived.
  `is_renewal`, added after the others by the store's third migration, is null in the
  rows written before it, which reads as unticked. The
  newest revision's row is the quote as the app lists, searches and orders it.
  Assigning, archiving, restoring, marking final and reopening each write a revision of
  the newest, its values, request, prices and release kept and its details changed: the
  pipeline sees the new details, as any other input, the next time the quote is priced.
  A new quote is priced before it takes its reference, so its first pricing sees none. A
  handover note is an event alone, written with the quote's row held as a revision holds
  it, so a note never lands on a quote archived before it. A quote's name
  (`quote_name`) is at most 200 characters, a longer one refused naming the limit at
  any save.
  Who marked it final and when, when it was archived and by whom, the note, and the
  quote and revision it was made from and how, a duplicate, a renewal or an
  alternative, are read from its events; its version is its newest revision's number.
  Each change but a note names the version it was made on and is refused, with nothing
  written, when the quote has moved on, naming who moved it, and answers with the quote
  as its own transaction left it; each writes an event of who did what and when in the
  transaction that makes it; a save
  carries an operation key, and one repeated with a key already written answers what
  that key wrote, the quote as that write left it at the revision it wrote whatever was
  saved or noted since, with nothing priced or written again; two at once with one key write once,
  the second answered as a repeat. A final quote takes no save until it is reopened,
  giving a reason, which names the revision pinned final; marking one final needs its
  newest revision priced on the running release; an archived quote takes no change but
  being restored; and a final one stays final through being assigned, archived and
  restored, each a revision of its own that keeps the pinned final. The app's lists
  are read a page at a time: the quotes assigned to the signed-in underwriter, the
  team's, or the archive, of one stage when asked, matching every word searched for in
  the reference, quote name, insured or broker, newest saved first, newest reference
  first or by insured, with each list's count and each stage's. A
  saved quote opens on its own form, and its values are carried onto the current form
  through the stable ids: a renamed table or column keeps its values, each filled
  value whose table or column the current form no longer has is named as
  `table.column`, by the names it was saved under, and a table that now holds one row
  keeps its first, each filled value of the rows after it named as `table.column row N`. A database is brought to the schema by
  the store's migrations, numbered and shipped with Haute, so a Haute upgrade carries
  them; the store refuses to open a database behind the newest, naming what to run, and
  nothing migrates as it opens. A migration keeps the previous release's reads and
  writes working where it can: a column it adds is nullable or has a default, and it
  drops nothing; and the database records the oldest schema whose Haute can still use
  it, so a Haute behind the database's schema opens it when its own newest is at least
  that, as a release rolled back after a migration does, and refuses it, naming the
  upgrade, when it is not. A migration after which the previous release could not read
  what this one saves names itself as that oldest schema, so the previous release
  refuses the database outright rather than failing on the quotes it cannot read: the
  third does, as the sheets a quote is saved with now hold `is_renewal` in
  `quote_details`, which a Haute at the second refuses. Rolling back past such a
  migration means restoring the database as it was before it. A SQLite store keeps foreign keys on and its rollback journal, never
  write-ahead logging, so the file stands alone between writes.

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
- **A gesture is one step.** A drag replaces the form on each move without history, as
  the graph store's raw setters do for the canvas, and records the form as pressed once
  it is released, so undo reverses the whole drag, and a drag that changed nothing, held
  at a limit or brought back, records nothing and leaves redo standing; a burst of nudges
  is grouped the same way. An undo, a redo or another edit made during a drag finishes the
  drag first, so undo and redo never change direction: Ctrl+Z during a drag undoes the
  drag, and the release does nothing more.
- **One reorder.** The step editor's cards, a component's fields and the sheets' tabs are
  dragged into order through one hook, a row dropped on another put there, rather than a
  second drag model with markers of its own.
- **Priced once entered.** The sample and the app's quote are priced 300ms after the last
  value entered, as a spreadsheet recalculates once a cell is entered: a text or number
  cell's value reaches the quote on Enter, Tab or leaving the cell, so a half-typed number
  is never priced and each entry is one undo step, and the pause lets a run of entries,
  tabbing along a row, price once. While a cell is being typed in, the sheets show the
  pipeline's answer for the values entered.
- **The form as it stands.** Pricing asks the server for the tables of the unsaved form
  rather than reading the saved file, so a Collection shows the pipeline's answer for what
  is on the sheet now, as a rater's cell would; the pipeline's own Workbench Input still
  follows the saved form, so a save is still what changes the pipeline.
- **One sheet, three sets of values.** The components draw a new quote, the sample and
  the app's quote through one set of values given to the sheet, rather than a body for
  each: what a cell holds, how it changes, what the last pricing gave and which cells are
  marked come from a new quote's defaults while building, from the form store in Preview and from the
  Preview store in the underwriter app, so a change to how a cell looks or a row is
  matched is made once.
- **Build is a new quote, Preview the quote.** Building draws the sheets as an
  underwriter first meets them, so a layout is judged on what a new quote shows, and the
  values an analyst keys in live in Preview, which draws the sheets as the app does. The
  sample is typed there because it is the quote Preview prices and the pipeline's previews
  run on: one set of values, saved with the form, rather than a sample and a second quote
  that lasts only while the editor is open.
- **A default starts a new quote, and is not typing.** A default is what a quote holds
  before the underwriter types, so it fills a new quote alone: the sample is a quote
  already typed, and a quote made from another carries that one's values. Counting a
  default as typing would price a new quote, and mark its required cells, before anyone
  touched it, so a quote holding only its defaults waits as a blank one does. A many-row
  table's rows are started by typing into them, and defaults for them would make a row of
  defaults alone a row priced, so its columns take none for now.
- **Priced as typed, never stopped by the rules.** The sample and the app's quote are
  priced once entered, so what the sheets show is always the pipeline's answer for
  what is entered on them and nothing needs pressing, and an underwriter's Save keeps the price the
  values settle on. A column's rules mark the cells that break them but never stop a
  pricing: refusing a quote is for validations, to come. Clear is gone: it emptied a quote
  at one press, and Preview's quote is the saved sample, while Undo takes back what was
  typed.
- **Rows matched as the server typed them.** A Table's output rows are matched through
  the typed sample the pricing answers with, not the text in the grid: the server leaves
  empty rows out and numbers the index as the grid does, so the filled grid rows are its
  typed rows in order, and a key typed as "$1,000" matches an output row holding 1000.
- **A Chart is a picture of the sheets.** A Chart plots the values the components are
  given, the sample in Preview or the app's quote, with their price, rather than
  values of its own, so it shows what was typed and what it priced at together, its rows
  lined up as a Table's are and dimmed as its output cells are. Its fields are held and
  lined up as a Table's are, by table and column id, so a column renamed, retyped or
  removed reaches it as it reaches them, and its numbers take their columns' formats,
  since how a column shows is the column's; a value axis shared by columns of different
  formats can take none of them, so it shows compact numbers.
- **What shows is what is priced.** A hidden value the pipeline still received would price
  what the underwriter switched off unless the pipeline repeated every condition, and a
  quote ticked, filled and unticked again would price differently from one never ticked.
  Leaving hidden values out makes a quote what its sheets show, whatever order it was
  typed in; keeping them, rather than clearing them, means hiding a section and showing it
  again loses nothing. The server leaves them out where it makes the quote, the one place
  the sample priced in Preview, the Workbench Input's copy and the app's quote all come
  from, and the view, which hides and shows as each value is typed, applies the same
  rules, the two held to one set of cases both test against.
- **Decided at once, by hiding.** Conditions test the values as typed, except that a
  condition on a column no component that shows has never holds, so hiding only ever hides
  more: starting from everything shown and hiding until nothing changes gives one answer
  whatever the order of the sheets and components, a chain of conditions (a sheet shown by
  a box on a sheet shown by another box) is followed without being written twice, and no
  value the underwriter cannot see decides what they see.
- **A Chart takes nothing.** A quote holds what can be typed into the sheets that show, so
  a column counts for Shown when only on the components it is typed into. Were a Chart's
  columns to count, a Chart that plots a section's values would keep them in the quote
  after the underwriter switched the section off, and a condition could hold on a box
  nobody can tick.
- **Drawn as Explore draws.** A Chart draws through the ECharts runtime Explore's charts
  load, in SVG with their theme, decals and reduced motion, and only once a sheet shows a
  Chart, so the editor pays nothing for charts until then; it builds its own options,
  since it plots a sheet's values rather than a pivot's result. The runtime registers Pie
  beside Bar and Line for it.
- **A Heading names a section.** A section of a sheet is often several components, the
  cover's details in a Collection beside its schedule in a Table, so the name above them is
  a component of its own rather than one of their titles, placed, hidden and shown as they
  are, so a section's heading can hide with it. It is drawn as text on the sheet, not in a
  frame, so it reads as the sheet's structure rather than something to fill in, and it
  takes one of three sizes, a title, a heading and a label, and one of three alignments,
  rather than a free font size or position of its text, so the sheets look alike however
  many hands lay them out. Its description is the line a section often needs under its
  name, what to enter there or what it covers, kept with the name so the two move, align
  and hide together; it is the Heading's, never a heading of its own, so a Heading without
  text is a problem whatever its description says. A section's own title is the builder's,
  for its bar and its panel; what the underwriter reads at its top is a Heading in it,
  which hides with it.
- **Sections close up, components keep their place.** A component hidden in the middle of
  a freely laid out sheet leaves a hole. Working out from free positions what may close up
  fails somewhere in every rule tried: a hole stays beside a tall component that still
  shows, a component moves because of what hid in another column, or the reading order
  changes. Other free canvases show the same: Retool and Appsmith close up a hidden
  component column by column, and components laid side by side drift apart, while
  Grafana refuses show/hide rules on its free layout to avoid the gaps. A section is a
  strip the builder draws across the whole sheet, so closing one moves everything under it
  by the same amount: rows stay aligned, the reading order stays and nothing comes to
  overlap, and the underwriter never sees a layout the builder did not make. One Show when
  on a section hides its heading, its fields and its chart together, rather than the same
  conditions written on each. A hidden component keeps its place within its section, which
  suits one beside another that stays; the builder gives what should close up a section of
  its own. A section is added from the palette like any component and dropped where it
  should start, its line never cutting through a component, so cutting an existing sheet
  into sections is one drag per cut, and it is named, selected, moved and sized on the
  sheet itself, by its bar and its handle, not by controls beside the sheet. A component
  moved stays inside its section, so a move never lays one across a border or moves a
  border; a component that grows, resized, pushes its section's border down, and a section
  never shrinks of its own accord. While building, every section keeps its place under its
  bar, faded while hidden, so it can be edited; Preview stacks those that show, with no
  bars.
- **Nothing moves under the underwriter.** A section that opens or closes moves what is
  under it while the underwriter works. A text cell commits as it is left, and pressing a
  tick box or a button leaves it, so the commit could move the press's target between
  press and release and the browser would drop the click: the layout holds while the
  pointer is pressed. A section opening or closing above the place being worked on would
  move it off the screen, and the browser's own scroll anchoring does nothing for
  components placed by position, so the view scrolls by as much itself. Neither animates,
  as a target moving under the pointer is easier to miss.
- **Read as laid out.** A screen reader reads the sheet, and Tab moves through its
  components and cells, in the order the page holds them, while a builder adds components
  in whatever order the work goes. Holding them in the order they sit, section by section,
  then row by row and left to right along each row, as other canvas builders order theirs,
  means a Heading placed
  above a section is read before it and an underwriter tabbing through a quote moves down
  the sheet. Rows rather than a strict top-to-bottom sort, because components laid side by
  side are rarely snapped to one top: a Table a grid step higher than the Collection to its
  left would otherwise be read first. Which component is drawn over another where two
  overlap stays the form's order, the order they were added in but for a copy, just over
  its original, set apart from the reading order, so laying a sheet out never changes what
  covers what.
- **Problems, not refusals.** A condition is written against the schema, which changes
  under it as columns are retyped, given other allowed values or their table moved between
  one row and many, so a condition that no longer fits is reported where it is, as a field
  showing a table of the other kind is, and never holds until it is fixed, rather than
  refusing the form or quietly dropping the condition and showing what it hid. Removing a
  column is the one change that takes its conditions with it, as it takes its fields,
  after the schema editor has asked.
- **The view's state is the view's.** Which sheet shows and which of the view's own
  sections (the sheets, the schema or Preview), the selection (a component, a sheet's
  section or the sheet's own panel), the zoom and the panel's width are neither saved with
  the form nor undone with it: they live in a store of their own, read against the form, so
  a sheet, a section or a component that disappears from the form is simply no longer the
  one showing or selected.
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
- **Preview's drawing, the app's data.** Preview already shows the sheets as an
  underwriter uses them: nothing selected or moved, the tabs read-only, no section bars.
  The app mounts the same components over the same stores, which it fills from its own
  routes, its quote in the sample's place, priced as it is typed as the sample is, so what
  an analyst sees in Preview is what an underwriter sees, and the builder's palette,
  toolbar, schema editor, panels and shortcuts are never mounted.
- **One release.** The form, the sheets and the pipeline ship in one release, so form
  v12 prices on pipeline v12 by construction, and a page that holds another release's
  form is refused rather than priced on a contract it was not drawn for.
- **One store, two databases.** An analyst saving a test quote and an underwriter saving
  a quote go through the same code, so what an analyst's machine and CI prove holds for
  the underwriter app; only the connection differs. SQLAlchemy Core and Alembic, which
  MLflow already installs with Haute, speak to SQLite and the server engines alike.
- **A quote keeps its form.** The current form cannot show a field it no longer has, so
  each revision names the form it was keyed into, kept under its content hash, the
  revision the editor already gives a form, once however many quotes name it.
- **Migrations Haute owns.** The schema is Haute's, not each project's, so its
  migrations ship with Haute and a project takes a new one by upgrading Haute. A deploy
  applies them before the new release starts, never the app as it opens, so replicas
  never race to migrate.
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
  checkbox and the icon select); and the fields' order and the sheets' tabs use the
  list-reorder hook the step editor's cards use.
- [engineering-quality](../engineering-quality/high-level.md): the generated contract bundle
  carries the `workbench` response group and the underwriter app's `workbench-app`.
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
- [frontend-preview-explore](../frontend-preview-explore/high-level.md): a Chart draws
  through the lazy chart runtime and the theme Explore's charts use.

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
  file that does not exist is the blank form. A sheet holding `widgets` of its own is not
  a form: "<form> is not a workbench form: pages.N.widgets was removed; a sheet's
  components now sit in its sections (pages.N.sections[].widgets)", N the sheet's place.
  Nor is a section that a component reaches below (its `y` plus its `h` above the section's
  `height`): the message names the section's place and the component; nor a sheet a
  component reaches past (its `x` plus its `w` above the sheet's `width`), or one without a
  `width`: the message names the sheet's place, and the component. A fetch of the tables that fails shows one
  error toast and changes no Workbench Input or Workbench Output; a fetch made for an
  earlier document never changes the current one, and an update it started that is still
  waiting on identity resolution is dropped with the commit controller's usual message. A
  read of the form that fails is shown in the view in place of the schema editor, with a
  way to try again.
- `GET /api/workbench/params` answers 404 while the workbench is not enabled, and 409
  with why a file or a column cannot give values: the project has no pipeline folder,
  the name is not a CSV directly in `files/params/`, the file does not exist, is empty,
  is neither UTF-8 nor Windows-1252 or cannot be read as a CSV, or the column is not in its header, holds no
  values or holds more than 5,000. `haute app build` refuses a form with an input column
  whose file can no longer give its values, naming the form and the column, before the
  release directory is touched.
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
  be removed, and the view never offers to. A section's components are deleted with it
  too, after a confirmation; a sheet's last section cannot be removed, and the view never
  offers to. A layout value outside its range (a Table's
  rows 1 to 50, a Collection's columns 1 to 12, a section's height below 48 or below what
  its components reach, a sheet's width below 240 or below what its components reach) is
  refused at the field, which says the
  range. A component dropped outside the sheet's viewport is not added. Fit never takes
  the zoom below its minimum: a sheet too wide to fit at 25% fits at 25%.
- A Chart's problems are refused nowhere: the form reads and saves with them, and the view
  names each on the dashed frame while building. A field that is not a number or is an
  index, a field whose rows do not line up with the first field's and a field gone from
  the schema plot nothing; a column labelling the rows that does not line up, or is gone,
  leaves them numbered; a pie of many rows plots its first field. A Chart that cannot be
  drawn, its runtime failing to load or its options refused, says why in its place, and the
  rest of the sheet is drawn as ever.
- A Heading with no text (none, or only spaces) is a problem refused nowhere either: the
  form reads and saves with it, the view names it on the dashed frame, and Preview draws
  no text in its place, only that frame.
- `POST /api/workbench/tables` answers 404 while the workbench is not enabled, the
  structured 422 for tables the Quote Input's rules refuse and FastAPI's 422 for a body
  that is not a form; it writes nothing. A pricing that fails for any reason, a pipeline
  without a Workbench Output, a table nothing is connected to, a sample that does not fit
  its tables or the preview route's failure, shows as the toolbar's reason and empties the
  output columns, never as a toast; an answer that arrives after the view has left is
  dropped.
- A quote that breaks a column's rule is priced all the same: its cells are marked once
  anything is typed, and nothing is refused. A pricing of the app's quote that fails shows
  "Pricing failed: …" in its bar and empties the output columns, as the sample's does, and
  a save then keeps the quote unpriced.
- The schema editor refuses nothing: a problem is shown beside its table, and a save
  writes the form as it is. The tables route then answers the structured 422 for a table
  the pipeline cannot take, which the tables fetch shows as one toast, so the problem is
  visible in both views until it is fixed.
- A condition that cannot hold as written is refused nowhere: the form reads, saves and
  serves its tables with it, the condition never holds, so what it is on stays hidden and
  the values only hidden components show are left out of the quote, and the view names
  the problem on the sheet or component while building. A `show_when`, a condition or a
  test the form's shape does not have (an unknown `op`, a `values` entry that is not text)
  is a form that does not fit, refused as any other.
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
