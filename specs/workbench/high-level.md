# Workbench — High-Level Specification

## Purpose

The workbench is where a pricing team lays out the quote an underwriter keys in: a schema
of typed tables, the sheets, each a stack of sections holding Tables and Collections that
show those tables' columns, Charts that plot them, Headings that name them and Imports
and Exports, the buttons laid out to fill them from a file or a database and to take them
out, and the sample quote typed into them in Preview. It is part of Haute, switched on per
project in `haute.toml`, and its form is a folder of files in the pipeline folder,
`workbench/`, versioned with the pipeline.

The workbench's schema is where the pipeline's quote tables come from. The pipeline reads
them through a Workbench Input, a node type of its own beside the Quote Input, which takes
them, and the sample quote its previews run on, from the form folder wherever the pipeline
runs. The schema's output tables are what a priced quote fills in: the pipeline fills them
through a Workbench Output, a node type of its own beside the Quote Response, whose input
ports are those tables, read from the form folder alike.

This component owns the switch, the form folder with its reading, its saving and its
revision, the routes that serve the form and its tables to the editor, the
`haute init --workbench` scaffold, the Workbench view in which the form is edited inside
Haute (its host beside the pipeline editor, its toolbar and shortcuts, its schema editor,
its sheets, drawn while building as an underwriter's new quote opens, and Preview, the
sheets as an underwriter uses them, with the sample typed into them and priced live), and
the two workbench node types: where their tables come from, how the editor shows them,
what a Workbench Input gives without a request and how a Workbench Output's frames fill
the response.

## Scope

In scope:

- The `[workbench]` table of `haute.toml`: whether the project's workbench is enabled.
- The form: its canonical shape. The form folder: the files that spell the form in the
  pipeline folder, how they are read and written, and the blank folder
  `haute init --workbench` scaffolds.
- The workbench's tables as the pipeline holds them: the schema's input tables, each its
  name, its rows per quote and its typed columns, the sample quote as a request holds it,
  and the output tables in the same shape.
- `GET /api/workbench`, the status the editor reads, `GET /api/workbench/tables`, the
  tables, sample and response tables as the saved form defines them now, and
  `POST /api/workbench/tables`, the same of a form the view holds, saved or not.
- `GET /api/workbench/form`, the form with its folder's revision, and
  `PUT /api/workbench/form`, which writes the form's files unless one changed since they
  were read.
- `GET /api/workbench/params`, a params file's header and a column's distinct values,
  which a column's allowed values are read from (and Banding's categorical rules imported).
- The Workbench view: how it is shown beside the pipeline editor and what the pipeline
  editor does meanwhile, its toolbar and keyboard shortcuts, its schema editor, its sheets
  (the canvas and its sections, the Table, the Collection, the Chart, the Heading, the
  Import and the Export, their palette and properties panel, and the sheet tabs), drawn
  while building as a new quote,
  Preview, where the sample is keyed into the sheets as an underwriter keys a quote and
  priced live on the pipeline, and how the form is read into it and saved from it.
- The Workbench Input, the node type that reads the tables and sample from the form folder,
  which the editor shows, and previews on.
- The Workbench Output, the node type that reads the output tables from the form folder,
  which the editor shows, and whose ports take the frames that fill them.
- The quote store: saved quotes as rows of the form's own tables, each quote's latest
  state, on a SQLite file, its tables derived from the form and the activity of each quote.

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
- Where a deployment's quote store is configured, how a form change reaches its tables
  ([WB-42](../roadmap/workbench.md#wb-42--the-stores-schema-changes-and-environments)),
  and the server databases it will run on
  ([WB-43](../roadmap/workbench.md#wb-43--the-store-on-sql-server-and-postgresql)).
- Running an Import or an Export: reading a file or a database into a table
  ([WB-09](../roadmap/workbench.md#wb-09--actions-that-fill-the-sheets)) and writing the
  quote's tables out ([WB-11](../roadmap/workbench.md#wb-11--exports-from-the-app)).
- `haute init`'s other scaffolding ([cli](../cli/high-level.md),
  [pipeline-config](../pipeline-config/high-level.md)).

## Behaviour

- **Enabling.** A project's workbench is switched on in `haute.toml`:

  ```toml
  [workbench]
  enabled = true
  ```

  Without the table the project has no workbench and the editor looks exactly as it does
  without this component. With the table, `enabled` is required and `true` or `false`; any
  other key is refused by name, and `form`, where an earlier Haute named the form's one
  file, is refused saying that the form is the pipeline folder's `workbench/` now. The
  form is the folder `workbench/` in the pipeline folder, the folder of the pipeline file
  `[project]` names (`rating/workbench/` in a new project), never configured. `haute
  deploy`'s whole-file check of `haute.toml` accepts the table, and deploy reads the form a
  pipeline's Workbench nodes take their tables from strictly, refusing one it cannot read or
  that does not fit the pipeline ([deploy](../deploy/high-level.md)).
  `haute init --workbench` writes the table and a blank form folder in a new project, and
  in a project that already has a `haute.toml` it adds the two and nothing else, refusing
  when either is there; either way a path of the folder reached through a link or a
  junction refuses it, nothing written. Nothing else makes the folder. The deploy workflow `haute init`
  generates for GitHub or Azure DevOps starts when a file in the pipeline folder changes,
  the form's files among them; `haute init --force` rewrites `haute.toml`, so the table
  stays only when `--workbench` is given again, while the form folder is left as it is. A
  nested `[workbench.app]` table says where the
  underwriter app runs: `host` is `azure-container-apps`, and `edit_role`, when given, is
  the role the host signs underwriters in with that may change quotes, every other
  underwriter reading them; any other key, host or role that is not a name is refused by
  name.
- **The form.** The form is one document as the editor, the routes, a release and the
  quote store hold it: `version` (1), `name`, `schema` (its tables), `pages` (its sheets,
  at least one) and `sample` (the quote typed while building, `{}` for none). The form
  folder spells it as files (below). Every field has one canonical spelling and an unknown
  field is refused. A schema table has an id, a name, a `role` (`input`, sent to the pricing engine,
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
  details, `quote_details`, first: one row, an input, a quote's administration whatever the
  company or line of business, its columns `quote_reference`, `quote_option`,
  `quote_name`, `insured`, `broker`, `inception_date`, `expiry_date`, `is_renewal`,
  `stage`, `assignee`, `archived`, `created_date`, `created_by`, `modified_date`,
  `modified_by` and `haute_version`, in that order, `inception_date`, `expiry_date`,
  `created_date` and `modified_date` Dates, `is_renewal` and `archived` True/false,
  `quote_option` an Integer and the rest Text, each with the id `qd_` and its name. A form
  read or saved without it takes it on, and one holding it as an earlier Haute made it is
  brought to today's by id: a column under the name an earlier Haute gave it takes today's
  (`reference`, `quote_reference`), one Haute makes no more is dropped (`valid_until` and
  `currency`), those it holds keep what the project gave them, the missing ones are added,
  and all take today's order, the data it was read from left as it was. That holds wherever
  a form is read: the project's folder, the editor's save and a release's form. A field, a column
  look or a chart category on a column dropped shows Missing column in the editor and is
  not drawn in Preview or the app, a condition on it is false, and sample values under its
  id are ignored. One whose
  `quote_details` is not Haute's (the name on another table, or its role, rows, keys or
  index changed, or a column of another id, or one of its own renamed or retyped) is
  refused, as is a rule or a default on `quote_reference`, `quote_option`, `stage`,
  `assignee`, `archived`, `created_date`, `created_by`, `modified_date`, `modified_by` or
  `haute_version`, which the underwriter app sets. Its labels, groups,
  help and placeholders are the project's, as are the rules and defaults of the columns the
  underwriter types; the sheets show its columns, and the pipeline receives it, as any other
  input table's. Every other table leads with the quote's identity: `quote_reference`
  (Text) and `quote_option` (Integer), named as `quote_details` names them, the ids the
  names in every table, with nothing else of theirs set. A form read or saved without them gives them to
  each table at its head, unless the table holds a column of its own under either name,
  which keeps its place and values, the table going without them: that clash is named
  among the schema's problems and refused by building the app and by deploying
  (`identity_clashes`), and renaming the column gives the table the identity at the next
  read or save. A form whose identity columns are not its tables' first two, as Haute
  makes them, is refused. A sheet has an id, a title, its `width` in pixels, when it
  shows (`show_when`) and its sections (`sections`, at least one): strips across the sheet's
  whole width, stacked in
  their order, each with an id, a `title`, its `height` in pixels, when it shows
  (`show_when`) and its components (`widgets` in the document): a Table (`tableInput`, a grid of at
  least `rows` rows showing columns of many-row tables, with its look, whether its rows
  are fixed and whether it is transposed: `row_numbers`, `density` (`compact` or
  `comfortable`), `gridlines` (`rows`, `all` or `none`), `striped`, `fixed_rows`,
  `transposed` and `groups`, and its look set as a spreadsheet's is: `font_size` (10, 11,
  12, 13, 14, 16 or 18), its header's `header_font_size` (one of those, or null for its
  text's size, a pixel smaller in capitals), `header_bold`, `header_wrap`,
  `header_orientation` (`horizontal`, `up`, `down` or `stacked`), `header_color`,
  `header_font_color` and `header_align`, where its labels sit, its lines' `grid_color`, `grid_weight` (`thin`, `medium` or
  `thick`, 1, 2 or 3px) and `grid_style` (`solid`, `dashed` or `dotted`), its frame's
  `frame_color` and `frame_weight`, each colour `#RRGGBB` in capitals or null for the
  theme's, and `column_looks`, the looks of the columns given one of their own, each the
  column it shows by `table` and `column`, its label's `header_color`,
  `header_font_color` and `header_align`, its cells' `cell_color` and `font_color`, `bold`,
  `align` (each align `auto`, `left`, `center` or `right`), `width` (24 to 1000 pixels, or null)
  and `show_when`, when the column shows in the Table, as a component's (below), one for each
  column at most, none for a column it does not show and none that changes nothing), a
  Collection (`collection`, boxes
  showing columns of one-row tables, `columns` across, its `spacers`, the gaps among
  them, each the number of fields before it, in order and none past the last, and its
  look: `labels` (`above` or `beside`), `density` and `frame`, and, as a Table's are
  set, `font_size`, its labels' `header`, `header_font_size`, `header_bold`,
  `header_wrap`, `header_color` (their fill), `header_font_color` and `header_align`, and
  `label_width`,
  the percentage of its field a label beside its box takes; its boxes' borders' `box_style`, `box_weight` and
  `box_color`, as a Table's gridlines' are, `box_align`, where their values sit, and
  `box_width`; its frame's `frame_color`
  and `frame_weight`; and `column_looks`, its fields' own looks, as a Table's columns'
  are, a field's label's `header_color`, `header_font_color` and `header_align`, its box's
  `cell_color` and `font_color`, `bold`, `align`, its box's `width`, each width 24 to 1000 pixels
  or null, and its `show_when`), a Chart (`chart`, plotting number
  columns, with what labels the rows of its many-row tables, `category`, a schema column
  named as a field is or null to number them, and how it draws: `chart_type` (`column`,
  `line`, `area` or `pie`), `orientation` (`vertical` or `horizontal`), `stacked`, `legend`
  (`bottom`, `top`, `left`, `right` or `none`) and `data_labels`), a Heading (`heading`,
  its `text`, naming a section of the sheet, the `description` under it, its `size`,
  `large`, `medium` or `small`, and its `align`, `left`, `center` or `right`), an Import
  (`import`, a button an underwriter presses to fill an input table: its `label`, the text
  on it; its `source`, by `format`, a CSV (`csv`, its `delimiter`, `auto`, `comma`,
  `semicolon` or `tab`), an Excel workbook (`excel`, its `sheet` by name, `""` for its
  first, and its `header_row`, 1 to 100, the row naming its columns) or a database (`sql`,
  its `connection`, a named connection whose credentials the environment holds, `""` for
  none chosen, its `query`, and its `parameters`, each a `:name` in the query, by `name`,
  an identifier, given the value of a column by `table` and `column`, no name twice); the
  input table it fills, `table` by id, or null for none; how it fills a many-row table,
  `mode`, `replace`, `append` or `update` (its rows matched by the table's keys); and
  `column_names`, the columns of that table read from a column named otherwise, each by
  `column` id with that `name`, never blank, one each at most and none while it fills no
  table) or an Export (`export`, a button an underwriter presses to take the quote's tables
  out: its `label`; the `tables` it exports, by id, in the order chosen, none twice; and its
  `target`, by `format`, a CSV (`csv`) or an Excel workbook (`excel`), each with its
  `file_name`, its extension left out, `{name}` in it standing for the value of that column
  of `quote_details`, whether a column is headed by its name or its label (`headers`,
  `names` or `labels`) and whether its values are as the quote holds them or as the sheets
  show them, in their columns' formats (`values`, `raw` or `formatted`), and a CSV with the
  `delimiter` that splits its fields (`comma`, `semicolon` or `tab`); or a database (`sql`,
  its `connection` and its `table_names`, the tables written to a table named otherwise,
  each by `table`, one it exports, with that `name`, never blank, one each at most)),
  each placed in its section by its `x`, `y`, `w`, `h`, measured from the section's top-left
  corner and inside it, its `y` plus its `h` at most the section's `height` and its `x` plus
  its `w` at most the sheet's `width`, and with a
  `show_when` of its own, and each Table, Collection and Chart with a `title` and showing
  its `fields`, schema columns named by table id and
  column id so a rename keeps them. A `show_when` holds `conditions` and whether `all`
  of them must hold, `any`, or at least a whole number of them, 1 or more (`match`); with
  none, the sheet, section, component or field always shows.
  Each is a condition or a group, which holds `conditions` of its own, at least one and no
  group among them, and whether all of those must hold, any, or at least a number of them
  (`match`). A
  condition names a column of a one-row table by table id and column id, a test (`op`) and the test's `values`,
  as text: `ticked` or `not_ticked`, with no value, for a True/false column; `filled` or
  `empty`, with no value, and `equals` or `not_equals`, with one, for any other; `one_of` or
  `not_one_of`, with one or more, for a Text or number column; and `above`, `at_least`,
  `below` or `at_most`, with one, for a number or a Date column. A condition on a many-row
  table's rows names the table by id and the tests a row must pass, all in the same row
  (`where`), each a column of the table other than its index by id, a test and its values,
  as a one-row table's condition has them; and which rows: `any` of them or `every` one
  (`rows`), with one test or more; how many, by a test (`op`, `equals`, `not_equals`,
  `above`, `at_least`, `below` or `at_most`) and a whole number of rows, 0 or more
  (`count`); or the `sum`, `min`, `max` or `average` (`total`) of a number column's values
  in them (`column`, not the index), by such a test and a number typed as text (`value`).
  The sample holds, by table
  id, rows keyed by column id, each value as typed: text or a tick. The blank form, which
  `haute init --workbench` writes, is one sheet 1280 wide holding one section 560 tall,
  Haute's `quote_details` table alone and no sample.
- **The form folder.** The form is kept in the pipeline folder's `workbench/`, as files a
  reviewer reads:
  - `form.json`, the index: `version` (1, the folder's format, which fixes what each file
    holds and each setting's default), `name`, `theme` holding the colours set (left out
    while none is), `schema`, the tables' names in their order, `quote_details` first, and
    `sheets`, the sheets' names in their tabs' order.
  - `schema/<table>.json` for each table the index names, named by the table's name in
    lower case (no two tables' names differ only in case; the index keeps the name as
    spelled): its `id`, `role`, `rows` and `columns`, each column its `id`, `name` and
    `type` and what else is set on it. `schema/quote_details.json` holds only what the
    project sets on Haute's table: each column given something, by `name`, with what it is
    given (a label, group, help, placeholder or number format on any of them; a rule or a
    default on those the underwriter types); the table's id, role and rows and its
    columns' ids, types and order are Haute's and never written. The identity columns are written in no table. An output
    column holds no rule (`required`, `min`, `max`, `options`, `options_source`), no
    `row_per_value` and no `default_value`.
  - `sheets/<sheet>.json` for each sheet the index names, named by its title made a name
    (below): its `id`, `title`, `show_when` and `sections`, each section its `id`, `title`,
    `show_when` and `components`, each component its `id`, its `type` (`table`, which the
    document calls `tableInput`, `collection`, `chart`, `heading`, `import` or `export`) and
    its settings, but not where it sits.
  - `sheets/<sheet>.layout.json` beside it: the sheet's `width` and, by id, each section's
    `height` and each component's place, `{"x": 8, "y": 312, "w": 832, "h": 192}` on one
    line; an entry for every section and component of the sheet and for nothing else. A
    pull request that only moves or resizes things changes only layout files.
  - `sample/<table>.json` for each input table the sample has rows in, named as its schema
    file is: the rows as they stand, an empty row kept, since a row's place numbers it and
    lines it up with the values a column gives its table; each row's values by their
    columns' names and as typed, text or a tick, in the table's column order. A table with
    no rows has no file. A value under a name its table does not hold is kept as written
    and priced nowhere, as a value under a column dropped always was. A column given
    that name later takes the value at the next read; a row that holds the column's own
    value as well is refused at save, naming the row, since a file holds a name once.

  Only what is set is written. The keys that say what a thing is are always written: the
  index's `version`, `name`, `schema` and `sheets`; a table's `id`, `role`, `rows` and
  `columns`; a column's `id`, `name` and `type`; a sheet's and a section's `id` and
  `title`, with their `sections` and `components`; a component's `id` and `type`; every
  layout number. A setting with no default, such as an Import's source or an Export's
  target, is always written. Any other setting is written only when it is not its default,
  and a default written out by hand reads as that setting and is left out at the next
  save. The defaults are the folder format's: a different default is a new `version`.

  Files name things by name. A column is `table.column` (`br_rating.aop_deductible`) in a
  field, a Chart's `category`, a condition on a one-row table, a column look and a query
  parameter, and its bare name where its table is said: in the tests and the total's
  column of a condition on a many-row table's rows, and in an Import's `column_names`. An
  Import names the table it fills, and an Export the tables it writes, by name. A table or
  column keeps the id written beside its name for life: a rename changes the name and
  every reference to it and leaves the id, so saved quotes, which keep values by id, keep
  the column's; a table or column made anew takes an id of its own, though it takes an old
  name. A reference to a name the schema does not hold is kept as written: the editor shows
  it where it is, as it shows a field on a column dropped, `haute app build` and `haute
  deploy` refuse the form naming it, and nothing reads it as another column. A sheet, a
  section and a component keep the id their file gives them, unique in the form; the
  editor makes one from what it is (`sheet_`, `section_`, `table_`, `collection_`,
  `chart_`, `heading_`, `import_`, `export_`) and four random letters or digits
  (`collection_k3f9`), so a layout line says what it places.

  A sheet's name is its title in lower case, each run of characters other than ASCII
  letters and digits one `_`, none at either end, at most 64 characters: "BR Coverages" is
  `br_coverages`. Save refuses a form the folder cannot hold, naming each problem: two
  tables of one name whatever their case, two columns of one name in a table, a table or
  column name that is not an identifier (letters, digits and underscores, not starting
  with a digit), a table or sheet name Windows keeps (`con`, `prn`, `aux`, `nul`, `com1` to
  `com9`, `lpt1` to `lpt9`, whatever their case), a sheet title that gives no name or
  another sheet's, and a file whose full path would pass 240 characters. Every path a save
  writes or deletes is held to the project's write rules first, as every file Haute writes
  is: none through a link, a junction or a reserved name. Files are named in lower case, so
  a rename that changes only a name's case changes no file's name.

  Each file is JSON: UTF-8 text with LF line ends and a final newline, indented two
  spaces, its keys in one order, text as typed, a list of numbers on one line and a list of
  text or of objects one item a line, so every column, field and allowed value is a line
  of its own. Haute reads the folder whole whenever it needs the form and never keeps it,
  so an edit made outside the view reaches the next request; a read takes the files the
  index names and refuses any other file in the folder (Failure model). A read takes each
  file's bytes once and the revision is the hash of those bytes; every reader, in the
  editor, another Haute process or the command line, holds the form folder's lock, which
  every save holds while it writes, and reads again when the folder changed while it was
  being read by a writer that takes no lock, such as git outside Haute, so no read mixes
  two saves. A folder that does not exist is refused, pointing to version history, never taken
  for a blank form.
- **Status.** `GET /api/workbench` answers `enabled` and, while enabled, `form`, the form
  folder's path from the project root (`rating/workbench`). The editor reads it once when it starts. While the workbench is not
  enabled nothing in the editor changes on its account.
- **The tables.** `GET /api/workbench/tables` answers the schema's input tables as `tables`,
  the sample quote as `sample`, the output tables as `response_tables`, as the form defines
  them now, and, as `store_columns`, the columns the quote store keeps in each table that
  the form does not, each with what it holds, which the schema editor shows locked. It answers 404 while the workbench is not enabled; the tables and the
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
  Windows-1252 (as Excel saves a CSV on Windows), chosen from the file's bytes, trimmed,
  blanks left out, in the order the file first gives them; a column name holding a
  character an earlier save lost (U+FFFD) is refused, naming it, rather than offered as it
  reads. It writes nothing.
- **The form, served and saved.** `GET /api/workbench/form` answers the form as its folder
  holds it now and the folder's `revision`, the content hash of every file in the folder,
  its path and its bytes. `PUT /api/workbench/form` takes the whole form and the
  `base_revision` it was read at and, under the pipeline save's lock, as every write to the
  project is, so two saves never pass the revision check together, writes each of the
  form's files whose bytes differ from the file's, making the folders it needs, and deletes
  the form's own files it no longer has (a table's or a sheet's that went, a sheet's under
  its old name, a sample whose table's rows went), never a file the index did not name
  (a folder holding one is refused 409, nothing written);
  every write and delete is staged, and when one fails every file is put back as it was. It
  answers the form with its new revision. The files written and deleted are one
  save on the clone's save ledger, captured as the pipeline's save captures the files it
  wrote and through the same capture: no repository, no capture; no working branch
  chosen leaves the files uncaptured with a warning; a missing git identity leaves the
  files uncaptured and is reported for the editor to ask for; a capture that fails is a
  warning, and leaves the files uncaptured too. The response says what the
  capture gave (`git_sha`, `warnings`, `identity_required` and the files left
  `uncaptured`, as every write's response has them), the editor keeps an uncaptured file
  until the one capture route takes it, and from its first captured save on each file is
  tracked, so a milestone's sweep takes an edit made to it by hand. A folder whose revision
  is no longer `base_revision`, edited by hand or by a branch switch, is left as it is and
  the save is refused with 409 and a `stale_document_revision` detail, as the pipeline's
  save refuses a stale document; the view, when its last save lost its answer and the
  folder holds exactly what that save sent, takes the folder's revision and saves again,
  since that save is what changed it. A save always names a revision: the folder is there
  before the first one, made by `haute init --workbench`. The check, the writes and the
  capture hold the repository's lock, as a branch switch does. A form that is not one,
  that the folder cannot hold (The form folder), or whose files would not read back as a
  form, is refused 422 with the place and reason
  of each refusal, never the form sent back, and a form giving one id to two sheets,
  sections, components, tables or columns of a table is refused naming the id. Both routes
  answer 404 while the workbench is not enabled.
- **The view.** While the workbench is enabled, a view switcher at the bottom of the left
  palette offers "Pricing", the pipeline editor, and "Workbench".
  - **Showing it.** The Workbench view covers the area below the toolbar: its own left
    column, the component palette with the switcher back, and the sheets, which the
    schema's pane slides over from the left when the toolbar's Schema opens it. Meanwhile the pipeline editor stays mounted (live sync, the document and its
    undo history go on) but invisible and inert, its keyboard shortcuts, Ctrl/Cmd+Enter and
    React Flow's delete and pan keys off and its floating menus closed, so a key pressed in
    the view never edits the hidden pipeline. The Git, Assistant and Files panels open
    beside the view, in the properties panel's place. The Workbench Input's and Workbench
    Output's panels offer "Edit in Workbench", which shows the view.
  - **The toolbar** keeps the brand, the pipeline toolbar's source selector over its
    Pipeline control, and the project's controls (Pipeline files and Local files,
    Assistant, Help, the branch, Save and Commit) and shows the view's own, the same in
    both modes, so the toolbar keeps its layout as the mode changes: the form's Undo and
    Redo and Zoom In over Zoom Out, where the pipeline toolbar has its own Undo and Redo and
    zoom, then Build over Preview, Schema over Display and Reset, then, in Preview, why
    pricing the sample last failed; there is no Price and no Clear. Reset starts the
    sample again as a new quote (the sample, below). Schema opens and closes the
    schema's pane in either mode, pressed while it is open. A control the mode has no use
    for stays in its place, disabled, as the palette's components are in Preview:

    | Control | Disabled |
    |---|---|
    | Display | In Preview |
    | Reset | While building, and while the sample holds a new quote's values and nothing else |

  - **The schema's pane.** Schema opens the schema editor in a pane that slides in from
    the left over the sheet, under the sheet tabs, covering the sheet's left part without
    moving it; the part it covers takes no presses and no drops while it is open. Its
    right edge is dragged to resize it, from 320px to half the window, or to the width its
    fields take at full stretch where that is wider; it opens at that width, kept while the
    editor is open, and its Close, or Schema again, closes it. Opened again, it is as it
    was closed: scrolled where it was, the same tables collapsed, the same column's label
    and rules open and the same search. A search box at its top filters the schema as it is
    typed: only the columns whose name or label holds the text, whatever its case, show, in
    the tables holding one, each open though it was collapsed; with none, "No fields
    match". While it filters nothing is added, moved or collapsed (no Add column, Add table
    or index, no grips, no Alt+Up or Alt+Down, Enter in a name only committing it, the
    collapse toggles off), so each edit is to a column it shows; emptying it, by its clear
    button or Escape, shows the whole schema again as it was. It stays open as the mode changes and as another sheet shows, and opening it keeps the selection and the
    properties panel. While building it edits the schema; in Preview it shows the schema
    read only: a table still collapses and expands and a column's label and rules still
    open, but no field takes typing, no select takes a choice, and no table or column is
    added, removed or moved. Keys pressed in it are its own, never the sheet's.

  - **Save.** Save and Ctrl/Cmd+S save the project, the same from either toolbar, with a
    focused field's edit included: the form is saved first when it holds unsaved edits or
    a save the ledger did not capture (for want of an identity, or because the capture
    failed), then the tables are fetched afresh, so the Workbench Input's and Workbench
    Output's ports are the saved form's and a connection to a table it no longer has is
    removed (the Workbench Input, below), and then the pipeline is saved, so what is saved
    runs on the form that was saved. A fetch that fails refuses the pipeline's save, with a
    toast saying the pipeline was not saved, rather than save connections the form may not
    hold. A form save refused, as stale or failed, saves no pipeline, so the pipeline is
    never saved over a form the view does not show; and a save of a pipeline document that
    cannot be saved (read-only, or changed on disk under unsaved edits), one asked for
    inside a submodel, or one asked for while the assistant's turn runs, is refused before
    the form is saved, with the pipeline save's own reason, so the form is never saved
    ahead of a pipeline that is not: the fence that disables Save in both toolbars holds
    for Ctrl/Cmd+S and the git flows too, and is read again after each wait, so a submodel
    opened, the assistant's turn started or the document changed while the form was being
    saved or the tables fetched refuses the rest of the save, the pipeline unsaved until
    the next Save. A Save pressed while one runs waits for it and then saves the project
    as it stands, as the form's own saves queue behind one another. Save passes the pipeline
    editor's git readiness gate first, as it does there: with no working branch chosen, or
    a divergent one, the gate's modal opens and the save follows its confirmation. Each
    save is reported as the pipeline's is: "Saved → rating/workbench", the form folder as
    the status names it, and the pipeline's own toast, the ledger commit on the branch indicator, each warning in a
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
    its unsaved edits and history, across a trip to the pipeline editor, and reads the
    folder again whenever the pipeline editor adopts its document anew, as a branch
    switched by hand or a change on disk brings: a folder at the same revision changes
    nothing; a changed one replaces the form, its history dropped, while the form is as
    saved, and otherwise the view says the form changed on disk and offers Reload. A
    document adopted while the folder is still being read is read again after it, so the form never ends on the branch
    the read began on. A move reloads the page, and the view reads the form anew when it
    next shows; a switch in place from the Git panel reads the destination's form through
    that adoption, or at once when the switch was chosen over the form's unsaved edits. A
    form that cannot be read is reported in the view, with what is wrong and a way to try
    again.
- **The schema editor.** The form's tables, each with its name, its role (Input, sent to
  the pricing engine, or Output, returned by it; a table made an Output loses its columns'
  rules but Required and its sample rows, which only what is typed takes), its rows (One row per quote, or Many
  rows) and its columns; each column with its type, drawn as the step editor's marker for
  the kind (a number, text, true/false, a date) in the data preview's colour and changed
  from it, its name and, in a many-row table, whether it is a key. A column's label, group
  and help open beside it, the group offered from those its table's columns have, the boxes
  holding no hint text; for an Integer or a Decimal other than the index, its format, decimal
  places and thousands separators, separators ticked under General making it a Number as
  an Explore pivot does; and, in an input table, its default in a one-row table (ticked or
  not for True/false, one of the allowed values while it has some, a date for a Date, else
  typed, a number's default, and the allowed values the default offers, shown in its
  format, as a cell's are, and the default as typed while it is typed in), its
  placeholder for any type but
  True/false and Date and its rules: required, which an output column takes too, as marking a
  quote final needs a value in it, a range for an Integer or a Decimal, and the
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
  Under a column's name, a line sums it up whenever there is something to say: in any
  table, required; in an input table, its range in its own format, its default (in its format, or
  "ticked" for True/false), its allowed values counted with the file they came from and
  "a row each" for the column giving its table's rows; and, in any table, the format of
  a number column other than the index unless it shows numbers as typed (General with
  automatic decimals): the format's name, a currency's as its sign and code, its decimal
  places when chosen, and "no separators" when they are off.
  Every text field
  commits on blur or Enter, so an edit is one undo step; Enter in the last column's name
  adds the next column, and in any other only commits it, a new column, by Enter or Add
  column, taking the type of the column above it, Text first in its table or under its
  index, a column is dragged by its grip within its table, or moved by Alt+Up and
  Alt+Down in its name, a table likewise by the grip beside its name or Alt+Up and
  Alt+Down in it, and a new table's or column's name takes the focus. A many-row table can have an index: a column first in the table, `row_number`
  until renamed, an Integer whose type cannot change, numbering the rows from 1, which
  neither a drag nor Alt+Up and Alt+Down moves or passes; switching a table to one row clears its keys
  and drops its index, with what shows it, and switching it to many rows clears its
  columns' defaults. Removing a column or a table a sheet shows, a
  sheet's, a section's or a component's condition tests, or an Import or an Export uses,
  asks first, and takes it off the
  sheet, out of those conditions, out of the Imports and Exports and out of the sample: a
  column takes a test of a list's
  rows on it alone, the list's condition going with it once it has no test while it needs
  one (in any or every row) or when it totals the column; a group left with none goes with
  them; a table takes every condition on its rows; a column takes the name an Import
  read it from and the parameters it gave a value; and a table leaves the Import that
  filled it filling none, its column names gone too, takes the parameters its columns
  gave, and stops being exported, with the name it was written to. The editor says what would stop
  the schema being the pipeline's tables: a table's name is held to the port rules the
  Quote Input's labels are held to (an identifier, none of the document's reserved labels,
  unique whatever its case), a column's name is an identifier that is not a Python keyword,
  as the server holds it, unique in its table, a many-row table needs a key, an input
  column's range must not be inverted and its allowed values must be of its type and
  read from a params file, not typed in, as an older form may hold them, True/false and
  Date columns can have none, and a default must be a value its column
  takes: a tick for True/false, else text of its type, within its range and one of its
  allowed values. Haute's `quote_details` table, first, keeps
  its structure and its place: its name, role and rows and its columns' names, types and
  order are fixed, it is neither deleted nor given a column, and no table moves above it; its columns take labels, groups and help, and
  those the underwriter types their defaults, placeholders and rules, while the columns
  the app sets take a label, a group and help alone. Every other table's `quote_reference` and
  `quote_option` lead it, locked: not
  renamed, retyped, moved or removed, and taking no label, group, help, rule, default or
  format; a table added has them at once, and an index goes after them. They are not keys,
  and the sheets are never offered them: a component's fields, a Show when's columns and a
  Chart's rows leave them out. Under each table's own columns are those the quote store keeps
  in it that the form does not, quote_details' own and a many-row table's `quote_row`, each
  with what it holds and a lock, and nothing to change; a search shows none of them.
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
  While the sheet's own panel is open (the properties panel, below), the sheet has a
  handle at the middle of its right edge whose drag sets its width, on the grid, never
  less than its components reach nor than 240, and one under the middle of its bottom
  whose drag sets its height through its last section's, so the sheet ends where it is
  dragged to, on the grid, never less than that section's components reach nor than 48
  for it. While a section's handle or the sheet's is dragged, the viewport scrolls over no
  less than the sheet as pressed, so a sheet growing shorter or narrower never scrolls the
  edge away from the pointer; the scroll settles once the drag ends. Each is one undo step. A component stays inside its section: dragged, nudged, dropped
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
  shaded unless `striped` is off;
  transposed (`transposed`), the same grid on its side: a line
  per field, labelled at its start in the header's look, the labels staying in view as
  the rows scroll across and ruled off from them, and a column per row, numbered above
  unless `row_numbers` is off, the numbers staying in view as the fields scroll down; its
  stripes and gridlines are its lines' as drawn, a full grid lining its columns too, and
  an output field is ruled off above its line; showing groups (`groups`), while a field
  it shows is in a schema group, each run of fields side by side in one group headed
  by one cell naming it, the name centred whatever its `header_align`, a row of them
  over the labels with each name ruled under inside its cell, so groups side by side
  stay apart, and a blank cell over a field in none and the numbers, or, transposed, a
  column of them before the labels, which stay in view beside it; and its look as a spreadsheet's is set: its values
  at its `font_size`; its labels at their size, bold unless `header_bold` is off, wrapping
  across their column with whole words kept rather than cut off while `header_wrap`, and
  turned as `header_orientation` says, reading upwards, downwards or their letters stacked
  one under another, at the header's foot, centred across their column and setting it no
  least width, a turned label that wraps starting a line beside it past 128px, the group
  names staying level; its lines, the gridlines, the rule under its header, the line
  before an output and a group's rule, in its `grid_color` and `grid_weight`, the
  gridlines in its `grid_style`, the rules solid; its frame in its `frame_color` and
  `frame_weight`, the rule under its title in that colour; its header strip in its
  `header_color`, its labels and group names in its `header_font_color`, its labels across
  their column as its `header_align` says; and each column's
  own look over the Table's: its label's fill and font, its cells' fill, drawn over the
  stripes and the output shade, their font, on an index's numbers and a dash too, and
  weight, where its values sit across it, as its type puts them while `auto`, and where its
  label does, as the Table's `header_align` says while its own is `auto`, and as its values
  do while both are, and its width, exactly that wide, the
  grid as wide as its columns rather than its frame once every column has one;
  transposed, a column's look is its field's line, its label the line's label, and it
  has no width; a Collection as boxes, its columns across, their labels above
  them or, while `labels` is `beside`, beside them, a tick box in a box as the other fields
  are, the boxes compact (28px) or comfortable (36px) as its `density` says, and each
  spacer a gap across its whole width where it sits among the fields, the fields after it
  starting a new row below the gap; and its look as a Table's is set: its values at its
  `font_size`; its labels at their size, in capitals or as written, bold only while
  `header_bold`, wrapping with whole words kept rather than cut off while `header_wrap`,
  in their `header_font_color`, across their field as `header_align` says, at its start
  while `auto`, and, given a fill, drawn as a cell, padded, and beside its box as tall as
  it; a label beside its box `label_width` percent of its field wide,
  else two fifths of it, either never leaving its box narrower than 24 pixels, or what its
  box leaves while the box has a width; its boxes' borders in their
  `box_style`, `box_weight` and `box_color`, and each box `box_width` wide, else what its
  label leaves, never wider than its field, its value across it as `box_align` says, as
  its type puts it while `auto`, and a narrower box under its label across the field
  likewise, at its start while `auto`; its frame in its `frame_color` and `frame_weight`, the
  rule under its title in that colour; and each field's own look over the Collection's:
  its label's fill and font, its box's fill, over the input and output fills, font and
  weight, where its label and its value sit across it, each as the Collection's sit while
  `auto`, and its box's width; each of the two in a frame, a border round the panel's
  colour, unless `frame` is off, when it sits on the sheet as its title and fields alone,
  all in the sheets' colours (below);
  a Chart as its type draws the values it plots (Charts, below); a Heading as its
  text (Headings, below). Each field of a
  Table or a Collection shows its label (its column's, or the name made readable), a star for
  a required input, a mark showing its help when it has some, a hint at its type (a
  dropdown's chevron, a date's calendar), an empty cell blank or showing its column's
  placeholder (a number column's, while it is a number, in its format), a number in its
  column's format (a percent the value times 100, so a
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
  or is an index, a pie of many rows with more than one field, an Import's or an Export's
  (Imports and Exports, below), or one of its conditions',
  Shown when below) gets a dashed frame in the warning colour naming the problem, on hover and to
  a screen reader. One with no fields asks for them, while building, from the panel on
  the right; in Preview, which has no panel, it is its frame alone, as a Heading with no
  text is. The zoom runs from 25% to
  200% in steps of 10%; the sheet showing is fitted across the viewport, never past 100%,
  when the sheets first show for a form read (a zoom chosen since is kept across a trip to
  the schema or the pipeline editor) and on Ctrl/Cmd+1.
- **The palette.** The view's left column is Haute's palette shell with a Section and a
  Heading, in the structure colour, a Collection and a Table, in the entry colour, a Chart,
  in Explore's colour, an Import and an Export, in the data colour the Data Input and the
  Data Output have, and the switcher under them. A Section dragged onto the sheet
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
  at the bottom and no data labels, a Heading 480 by 48 of medium size, aligned left, an
  Import 160 by 40 labelled "Import", from a CSV whose delimiter is found from the file,
  filling no table and replacing a many-row table's rows, an Export 160 by 40 labelled
  "Export", of no tables, to a CSV named `{quote_reference}-{quote_option}`, headed by the
  columns' names, its values as held and its fields split by commas),
  showing nothing yet, and selected.
  A click adds nothing. Each item is a button in the tab order: Enter or Space adds its
  component, at that size, into the selected section, else the selected component's,
  else the sheet's first, at the first place free there (a grid step in from its top and
  left, 16px clear of the others, row by row from the top, else below them all, the
  section growing to hold it), selected and given the focus so the arrow keys move it, one
  undo step; Section adds a section after the selected one, or the selected component's,
  else at the sheet's foot, selected. The palette shares the node palette's open state, so collapsing it
  in either view collapses both; collapsed, it is the reveal strip with the compact
  switcher under it.
- **The properties panel.** Selecting a component opens its panel on the right, Haute's
  side panel resized from its left edge, where the Git and Assistant panels open while
  they are closed: the component's kind and its title; its Show when (below); once it shows a field, its layout,
  the rows a Table's grid starts with (1 to 50), whether they are fixed and whether it is
  transposed, or a Collection's columns across (1
  to 12), a value outside the range refused at the field, a Table's look (its density,
  its text size, its row numbers, its groups and its stripes; its header's Case,
  Capitals or As written, its Size, Auto or one of the sizes, its Direction, Horizontal,
  Rotated up, Rotated down or Stacked, its Fill and Font, and whether it is Bold and
  wraps; its Lines, the Gridlines, their Style, Weight, Thin, Medium or Thick, and Colour;
  its Frame and, while framed, the frame's Weight and Colour; and its Columns, each in its
  order opened in turn to its own look, its header's Fill, Font and Align, its cells' Fill
  and Font and Align, each Align Auto, Left, Centre or Right, its Width, blank for Auto, a whole number of
  pixels from 24 to 1000 otherwise refused at the field, not while transposed, and Bold,
  with Clear while it has a look, which leaves its Show when, and then its Show when, edited
  as a component's is, a column with a look marked in the list and one with conditions
  carrying an eye there; a colour picked
  as the sheets' colours are, shown as it is picked and one undo step, a pick that an undo
  or a redo made while its picker has the focus finishes first committing nothing more as
  the picker is left, and a column that stops being shown losing its look and its Show
  when), a
  Collection's look, set as a Table's is (its density and its text size; its Labels,
  their Position, Above or Beside, while beside their Width, blank for Auto, a whole
  percentage of the field from 1 to 99 otherwise refused at the field, their Case, Size,
  Align, Fill and Font, and whether they are Bold and wrap; its Boxes, their borders'
  Style, Weight and Colour, their Align, and their Width, blank for Auto, a whole number
  of pixels from 24 to 1000 otherwise refused at the field; its Frame and, while framed, the frame's
  Weight and Colour; and its Fields, each in its order opened in turn to its own look, its
  label's Fill, Font and Align, its box's Fill, Font and Align, Width and Bold, with Clear while
  it has a look, and its Show when, as a Table's columns have them, marked in the list as
  theirs are; its colours picked as a Table's are, and a field that stops being shown losing
  its look and its Show when), a
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
  since a Chart plots numbers. A search box over the tables filters them as it is typed,
  as the schema's does: only the columns whose name or label, as the list shows it,
  holds the text, whatever its case, show, in the tables holding one, each open though
  it was collapsed and its collapse toggle off; with none, "No fields match". Emptying
  it, by its clear button or Escape, shows them all again; it is the component's own,
  kept as its panes change and empty for the next component selected. A ticked column
  the search hides stays ticked, the component still showing it. Fields are held by table and column id, in the order
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
  also move between. An Import's and an Export's panels are in the two panes too:
  **Config**, what it does, and **Display**, its Label, committed on blur or Enter, and its
  Show when. Config starts with its Format, CSV, Excel or SQL, three toggles the arrow keys
  also move between, a format chosen starting with a new component's settings for it but
  for an Export's file name, headers and values, which a CSV and an Excel workbook share.
  An Import's then holds its format's settings: a CSV's Delimiter (Auto, Comma, Semicolon
  or Tab); a workbook's Sheet, blank for its first, and Header row, a whole number from 1
  to 100 otherwise refused at the field; or a database's Connection and Query, each
  committed on blur, and Parameters, a row for each `:name` the query holds outside its
  quoted text and comments (a `::` cast is none), in the order it first holds them, each
  with a select of the columns of the one-row input tables, by table, each by its label,
  "None" while it has none, a parameter the query stops holding losing its column with the
  same edit, and a column given a parameter it does not hold, as a hand edit can leave
  one, a row of its own with a cross that takes it away. Then Into, a select of the input
  tables, "None" first, a table chosen
  dropping the column names given for the last; while it fills a many-row table, Rows,
  Replace, Append or Update by key; and while it fills a table, Columns, a row for each of
  the table's columns an underwriter types (neither the quote's identity nor its index,
  and in quote_details no column the app sets), each with its type's icon, its label, and
  the name it is read from in a text field showing the column's own while it has none,
  committed on blur or Enter, a blank one taking the column's name away, and a name given
  a column it cannot fill a row of its own, the column's label or "Missing column", with a
  cross that takes the name away. An Export's then
  holds a file's File name, a CSV's Delimiter (Comma, Semicolon or Tab), Headers (Names or
  Labels) and Values (Raw or Formatted), or a database's Connection; then Tables, a
  checkbox per schema table, with its role, ticked in order, and, to a database, beside
  each one ticked the name of the table it is written to, showing the table's own while
  it has none; unticking a table drops its name; and a table it exports that is no longer
  in the schema a row of its own, "Missing table", with a cross. Each change is one undo
  step. While building, the showing sheet's tab has a settings
  mark, and no delete of its own, that opens the sheet's own panel in the same place, with its
  title, its Show when and its layout, its width in whole pixels (no less than 240 or
  than its components reach, a value below that refused at the field) and its height in
  whole pixels, its sections' heights summed, the bars drawn while building no part of
  it, set through its last section's (no less than the other sections' heights and what
  the last one's components reach, or 48, a value below that refused at the field), and at its foot
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
  conditions, and Add condition adds one on the first column of a one-row input table, else
  on any row of a many-row one with a test of its first column but the index,
  with the first test its type takes and no value yet; Add group adds a group holding such
  a condition, its conditions all needed while the Show when needs any and any while it needs
  all, in a frame of its own with its own Add condition and a cross that removes it, as
  removing its last condition does. Each condition is a row of its
  column (a select of the one-row input tables' columns, by table, each by its label, and of
  each many-row input table's rows, "Rows of" the table, and, in the Show when of what takes
  nothing typed, below, the output tables' columns and rows, tested on the price), its
  test (for True/false "is ticked" and "is not ticked"; for Text "equals", "does not
  equal", "contains", "does not contain", "starts with", "ends with", "is one of", "is not
  one of", "is filled" and "is empty"; for a number those but the four on text within text and
  "is greater than", "is at least", "is less than" and "is at most"; for a Date "is on",
  "is not on", "is after", "is on or after", "is before", "is on or before", "is filled"
  and "is empty"), its value, the control the column's cell is (a dropdown of the allowed
  values, a date picker, else a text field, committing on blur or Enter), or for "is one
  of" and "is not one of" a tick box per allowed value, or the values typed comma-separated
  when the column allows any, a number's written without a thousands separator, which the
  comma would split: "1,000" in a number column's list is refused, saying to write 1000,
  and never read as 1 and 0; and a cross that removes it. A test of one value may take
  another field of its column's type in place of a value typed: beside the test, while
  there is such a field, a select of "Value" and those fields, a one-row input table's
  columns by table, or, in a test of a row, the other columns of its table but the index;
  a Date tested against another Date then takes a whole number of days added to that
  date, a negative number taking them away, up to 36,500 either way, `completion_date`
  "is after" `start_date` + 365 days. There is no test against today: a saved quote would
  show other sheets, and be priced otherwise, on another day, so a date field on the
  form stands in for it. A condition on a table's rows
  says which rows, Any row, Every row, Number of rows, or the Sum, Smallest, Largest or
  Average of a number column of the table other than its index, chosen beside it, then its
  tests of a row, all passing in the same row, each a column of the table other than its
  index with its test and value as above and a cross, and Add test; Any row and Every row
  need a test, so their only one has no cross; Number of rows and a total end with their
  test, a number's ("equals" to "is at most"), and a whole number of rows, 0 or more, or a
  number, typed into a field that refuses anything else.
  Choosing another column, for a condition or a test of a row, keeps
  the test while the column's type takes it, else the type's first, the values while
  they fit the test, and the field it is tested against while that is of the column's
  type and not the column itself; a test that takes no value or several leaves the field
  and its days. Choosing a table's rows starts Any row with a test of its first column
  but the index, or, for a condition on a column of the table, a test of that column as
  the condition has it, against the same field while that is another column of the table;
  choosing a one-row table's column for a condition on rows keeps its test of that column,
  if it has one, against the same column of the table, which a test of a row names alone.
  Choosing Number of rows starts it at "is at least"
  1, a total at "is greater than" with no value, and Any row or Every row adds a test of the
  first column while there is none, each keeping the tests there are. While a condition's
  table has switched between one row and many since, its column select holds it, not
  offered, and its fault says so: choosing the table's rows, or a column, puts it right.
  With two or more conditions the section reads "Show when [all|any|at least] of
  these are true", and a group with two or more "[all|any|at least] of these are true", at
  least with a select of its number beside it, 1 to as many conditions as there are (or to
  the number, while it asks for more), choosing it starting at 2; one condition reads so
  too while the match is a number, as fewer conditions than it asks for is a problem to put
  right. Each change is one undo step.
- **The sheet's keyboard.** With a component selected: Escape
  deselects it, Delete or Backspace removes it, Ctrl/Cmd+D duplicates it (16px below and
  to the right, selected), Ctrl/Cmd+C copies it, a note saying so, and the arrow keys nudge it by a grid step, or a pixel with
  Shift, and with Ctrl/Cmd resize it from its bottom-right corner by as much, never under
  its kind's least size, its section and the sheet growing to hold it as the handle's
  resize grows them, a burst of nudges or resizes within 800ms being one undo step, kept
  inside its section, a nudge or resize that cannot change it recording nothing. Selected or not,
  Ctrl/Cmd+V pastes a copy of the component copied, as it was when copied but for the
  columns and tables the schema has lost since, which it no longer shows, tests, reads,
  gives a parameter or exports, as removing them takes them from it, onto the sheet
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
  own; its stripes, a light shade, show whatever is under them. A Table's own colours, its
  lines', its frame's, its header's and its columns', are drawn over the theme's, as a
  Collection's own are, its labels', its boxes', its frame's and its fields'.
- **A new quote while building.** While building, the sheets are drawn as an underwriter's
  new quote opens: every input cell empty but those a column's default fills, every output
  a dash, every Chart with nothing but defaults to chart, each grid showing the rows it
  starts with, and what shows decided by a quote holding its defaults alone (Shown when,
  below). The cells are inert, drawn as a new quote's
  rather than as a read-only quote's: they take no typing, and a press on one is a press
  on its component, which selects and moves it. Nothing is priced while building.
- **Defaults.** A new quote starts with each one-row input table's row holding its
  columns' defaults, as if typed: while building, and in the underwriter app's new quote.
  The sample keeps what was typed into it, and a quote made from another quote its
  values. A default is not typing: a quote holding nothing else typed, the details the
  app sets aside, is neither marked nor priced nor saved, as a blank one is not, and a value
  typed equal to its column's default counts as the default. A many-row table's columns
  take no default.
- **A row for each value.** A column of a many-row input table that has allowed values can
  give the table a row for each of them, in their order (`row_per_value`), such as a
  coverage table with a row for each peril: the table then has those rows, each holding
  its value in that column, a new quote's included, and since its allowed values come from
  a params file, the rows follow a refresh and `haute app build`. The rows are read so
  wherever a table's values are read, on the sheets and in what the server prices, whatever
  the sample or a quote holds: each value's row is the first holding it there,
  the rows holding nothing there fill the rows left in order, and any other row is left
  out. Each row is filled by its value, so once anything else in the quote is typed every
  one goes to the pipeline (while the sheets hide that column, its value is left out, as any
  hidden value is), a Chart plots each and a Table's output columns line up with each; but
  a row's value is not typing: a quote holding nothing else typed, a row left out included,
  is neither marked nor priced nor saved, and the server takes it as the empty quote, as it
  takes a blank one. A grid showing the table shows its rows, whatever its rows and fixed
  rows, with no row inserted or deleted, the column's cells showing each row's value as text,
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
  many-row tables' rows, row by row, with a delete on each row and a menu a right-click on
  a row opens, unless its rows are fixed or a column gives them (above): Insert, which
  offers Insert Row Above, Insert Row Below, Insert Multiple Rows Above and Insert
  Multiple Rows Below, the last two asking in a box how many rows, a whole number from 1,
  and Delete Row; transposed, the menu opens on a row's column, its number too, and says
  Column, Columns, Left and Right for Row, Rows, Above and Below. Rows are inserted, empty,
  and deleted in every input table the grid shows at once, as one undo step, so
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
  number's right-aligned. A text field and a date commit on blur or Enter, so a year typed
  digit by digit is one edit, a dropdown and a tick box on change, each commit one undo
  step. A dropdown holding a value its options no longer give shows that value, marked,
  since it is priced as held. Values are held as typed, text or a tick, by
  table id and column id; the server types them by their columns when the tables are made.
  Pressing a cell types there. The sample is part of the form: saved with it, undone like
  any edit, in Preview as while building, and dropped with a column or table removed from
  the schema. The toolbar's Reset, in Preview, starts it again as a new quote, as one
  undo step and without asking: each one-row input table holding its columns' defaults,
  nothing else, so each grid shows its starting rows, empty; a new quote's values alone
  are not priced (below).
- **Priced live.** While Preview shows, the sample is priced on the pipeline open in the
  editor whenever the schema, the sample or which of the sample's values are left out
  (Shown when, below) changes, 300ms after the last value entered (a text, number or date
  cell's when Enter or Tab is pressed or the cell is left, a tick or a choice as it is
  made), whenever what it is priced on changes while Preview shows (the pipeline, the
  Assistant's edits included, a submodel's definition, the active source, the document
  read again or another branch's, and a data file written through Files or a Data Input's
  source imported anew), and when Preview shows, as
  the pipeline may have changed meanwhile; a price made on anything since changed is
  dimmed until the next answer, and an answer to a pricing that started before such a
  change is shown dimmed too. Laying the sheet out differently prices
  nothing, and nothing is priced while building. A pipeline without a Workbench Input, which
  alone is given the sheets' values, is not priced: Preview says to replace the Quote Input
  the sheets never fill, or to add a Workbench Input. There is no Price and no Clear; Reset (above) starts
  the sample again as a new quote. The
  sample is priced whatever its cells hold, a value that breaks its column's rules
  included; with nothing typed in the cells the sheets show, the defaults a new quote
  starts with aside, it is not priced, as a deployed request never holds a blank quote,
  and its outputs show dashes with nothing said. Whether anything is typed is one rule,
  which Preview, the app's pricing and its Save, and the server all apply:
  a value in a column the sheets show, other than a column the app sets, the index, the
  column giving a table its rows and a column's default. A pricing asks `POST /api/workbench/tables` for the tables
  and sample Haute takes from the form as it stands, then previews the Workbench Output a
  table at a time through the preview route on the document as the editor holds it, the
  whole pipeline even while a submodel is open, with the active source and the form as it
  stands: every Workbench Input and Workbench Output of that request, at the top level or
  inside a submodel, takes the tables and sample the form sent defines, rather than the
  folder's, and nothing is written. One pricing runs at a time; asked again meanwhile,
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
  values is dimmed on every row. A Table of output columns alone has no typed rows to line
  up with: it shows a row for each of the first output table's answer, another output
  table's row matched by key, as a Chart of them plots them, none added or deleted; before
  an answer it shows its starting rows of dashes, so it reads as not yet priced or
  connected.
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
- **Imports and Exports.** An Import and an Export are buttons on a sheet: each fills its
  box, its label centred beside its format's mark (a CSV's, a workbook's or a
  database's), with no label its mark alone, in a frame of the sheets' border colour on the
  components' background, in their font, and is a button to a screen reader, named by its
  label. Neither shows schema columns nor takes anything typed. They are laid out and
  their settings chosen before anything runs them: reading a file or a database into a
  table is [WB-09](../roadmap/workbench.md#wb-09--actions-that-fill-the-sheets)'s and
  writing the quote's tables out [WB-11](../roadmap/workbench.md#wb-11--exports-from-the-app)'s,
  so in Preview and in the underwriter app each is drawn disabled, faded, and a press does
  nothing. What its settings say it is for: an Import reads the file the underwriter
  picks as they press it, a CSV split by its delimiter, found from the file while Auto, or
  a workbook's sheet under its header row, or runs its query on its connection, each
  parameter given the value its column holds, and fills its table, each column from the
  column of the name given it, else of its own name or label, whatever its case; a
  one-row table from the first row, a many-row table's rows replaced, added to, or
  updated by its keys, the rows no key matches added. An Export writes the tables it
  exports, a CSV of its one table or a workbook of a sheet per table named by it, under
  the file name with each `{name}` the quote's value for it, headed and filled as it says,
  or each to a table of the database of its own name or the name given it, the quote's
  rows there written again. The view names an Import's problems on its frame as any
  component's: no label; no table, a table no longer in the schema or an output table,
  which pricing fills; a column named that is no longer in the schema or that an
  underwriter does not type; replacing or adding to the rows of a table with a row for
  each allowed value, which only Update by key keeps; and, from a database, no
  connection, no query, a parameter given no column, a column no longer in the schema or
  not of a one-row input table, or a column given to a parameter its query does not hold.
  And an Export's: no label; no tables; a table no longer in the schema; more than one
  table to a CSV, which holds one; a file with no name or naming a column `quote_details`
  does not have; and, to a database, no connection. None is refused: the form is saved as
  it is.
- **Preview.** The toolbar's Build over Preview switches the view between building and
  the sheets as an underwriter uses them: the same sheets, their sections that show stacked
  one under the next and each component where it sits in its section, read and
  tabbed through in the order they sit, no section bars, their
  tabs only switching sheets, the components neither selected nor moved, the palette's
  components and the toolbar's Display disabled (the toolbar, above), no properties
  panel, the schema's pane read only while it is open, and the zoom. Either button leaves
  nothing selected and the schema's pane as it was. The sheets are drawn as the
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
  allowed values, a tick left in a column that is no longer True/false, and text left in
  one that now is, which the server would send as held and the pipeline refuse; a one-row
  table's one row is always checked, a many-row table's filled rows only, a cell the
  sheets hide never, and the index never faults. The
  marks never stop the pricing. Ctrl/Cmd+S still saves the project in Preview, Ctrl/Cmd+1
  fits the sheet, and Ctrl/Cmd+Z and Ctrl/Cmd+Shift+Z (or Ctrl/Cmd+Y) undo and redo
  outside a text field, as Undo and Redo do; the sheet's editing keys do nothing.
- **The underwriter app.** The sheets reach underwriters through an app Haute builds from
  the project, apart from the builder. `haute app build` writes a release: the pipeline's
  bundle as a deploy builds it (the sample left out), the form without its sample
  (`form.json`), each input column's allowed values from a params file read again from
  the file as the project holds it, what the release is (`release.json`: an id taken from the form, the
  bundle as it prices (where its nodes sit on the canvas names nothing) and the runtime it
  prices on, the installed versions an image would pin, the form's revision, the
  pipeline's name, the Haute version that built it and when) and an entry point,
  `app.py`, serving it through the app Haute maintains. A release whose files are not
  one is refused naming the file and what is wrong; a pipeline failing on a quote is
  answered with its reason.
  The release goes into a folder of its own: one that is or holds the project or the
  working directory, a file, or a folder holding anything but an earlier release is
  refused, and an earlier release is replaced only once the new one has built beside it,
  so a build that fails leaves it as it was.
  With `[deploy.container] base_image` set, pinned as a container deploy pins it, the
  release is also the image's build context, the container's Dockerfile copying it,
  and `haute app build --image TAG` builds the image with Docker; the image installs
  what a scoring container installs and the quote store's packages, SQLAlchemy and
  Alembic, pinned as the scoring runtime is, nothing more. The app
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
  model alone included: the page says it was drawn for an earlier release and offers
  Reload, with or without a quote store, an app without one saying that what is typed is
  not kept. The request is built as Preview's pricing builds it and priced through the deployed
  pipeline's engine, one quote per request, off the server's event loop, under the deploy's
  admission limit and a timeout, and answered whole: each output table's rows, with the
  request priced. The app prices at most a bounded number of quotes at once, four unless
  `HAUTE_APP_PRICING_WORKERS` says otherwise, a save's pricing among them: a pricing past
  its timeout is answered 504 but cannot be stopped, so it holds its place until it ends,
  and a pricing asked for while every place is held is refused at once (503, `Retry-After`
  saying when to try again) rather than queued behind them to time out too. `GET /api/app/release` and `GET /api/app/form` serve the release and the
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
  reader, so the sheets underwriters will see are checked before a merge. Served so, the
  app binds loopback and takes a request only for a loopback host (`localhost`,
  `127.0.0.1` or `::1`, on any port) and only without forwarded metadata (`Forwarded` or
  an `X-Forwarded-*` header), refusing any other (400) before it signs the request in:
  loopback keeps other machines out, and the host keeps out a page of a site whose name
  resolves to the analyst's machine, whose requests the browser counts as that page's own.
  No credential reaches the page.
  Given a quote store, the app keeps the quotes saved in it as rows of the form's own tables
  (the quote store, below). A hosted app keeps them in the environment its host names in
  `HAUTE_ENVIRONMENT`, one its release declares, at the database the host's own variables say
  it is at, and does not start on a database that does not hold its release's form's tables,
  which `haute app migrate --environment` makes before the release starts; `haute app serve`
  keeps its quotes in dev's store, `.haute/app/store.sqlite`, beside the release rather than
  in it, its tables built from the release's form as it starts, so a rebuild keeps them.
  Started with no environment named, the app keeps nothing, says so on `GET
  /api/app/release` (`quotes`), and answers its quote routes 503, naming `HAUTE_ENVIRONMENT`.
  A quote is named by its reference and option, the reference URL-encoded. The quote routes
  list a page of quotes (`GET /api/app/quotes`: the list, stage, search, assignee, order,
  cursor and size), create one (`POST /api/app/quotes`, made from another quote when it names
  one), open one (`GET /api/app/quotes/{reference}/{option}`: its values, its outputs as the
  pricing answered them, the request its values price as, and any preset row that no longer
  fits), save it (`POST /api/app/quotes/{reference}/{option}/save`), assign, note, archive,
  restore, mark final and amend it (`.../assignee`, `note`, `archive`, `restore`, `final` and
  `amend`, each answering with the quote as it left it), and read its activity
  (`.../activity`) and the people quotes are with (`GET /api/app/people`). A creation and a
  save each carry an operation key, and one repeated with its key is answered first, before
  anything else is checked; then each names the sheets' revision and the release it was
  drawn for, refused (409) for another. The details the app sets, `quote_reference`,
  `quote_option`, `stage`, `assignee`, `archived` and the quote's administration, are set
  over whatever the page sent, which is not read, a save stamped with one instant read
  before it is priced, and with nothing else typed there is nothing to save (422), what the
  sheets show decided with the app's details in place, as the page decides it, save a
  creation, which makes the quote holding what it starts with, unpriced, priced as it opens.
  A creation names the reference the underwriter keyed (`reference`), every creation but an
  alternative's, which takes its source's and names none (422 otherwise); a reference
  missing or breaking its rules is refused (422), and a reference another quote holds is
  refused (409, `reference_taken`) naming that quote. A save keeps the quote unpriced, or,
  given the prices the page showed, checks its values' types, prices it again on the server
  and keeps that pricing, saying whether it moved from what the page showed, so a price kept
  is always the release's own. Each change but a note names the quote's version and is
  refused (409) with the quote as it is now when the quote has moved on, or (409) naming what
  would let it when the quote does not take it as it stands; Mark final is refused (409)
  naming each required field without a value, or the release that priced the quote; a quote
  the app does not keep is 404. A write whose browser says it came from another site, or,
  from a browser that does not say, whose origin is not the app's host, is refused (403). The
  app's pages, `/`, `/quotes/new` and `/quotes/{reference}/{option}`, are its one page, which
  reads its route in the browser.
- **The Quotes page.** With a quote store, the app opens on the Quotes page, at `/`. On
  the left are the lists, My work (the quotes assigned to the signed-in underwriter), Team
  (everyone's) and Archived, and the stages of the list showing, Draft and Final, each with
  its count; a stage picked narrows the list and picked again widens it. Above
  the list are its name, a search box (`/` focuses it) matching every word typed in the
  reference, quote name, insured or broker (`ABC-1/2` matching that option alone, a
  reference matched whatever the case of its letters), in Team an assignee to
  narrow it to, the order (newest saved, Reference, A to Z with each reference's options in
  order, or insured) and New quote (`N`), which opens the new quote's window over the list;
  neither key acts from a control it is typed or picked in, so `N` in a select picks its
  option. The list is a table at Haute's
  density, 50 quotes at a time with Load more, read from the quotes' `quote_details`: each
  quote's reference and option, `ABC-1/2`, its title (its quote name, else its insured,
  else its reference and option), its insured, broker and inception date under their columns'
  labels on the sheets, the date as a day, its stage, who it is with and when it was saved,
  relative, with the time on hover. Relative times in the app read as everywhere in Haute:
  "just now" under a minute, then minutes, hours and days ago ("5m ago", "3h ago", "2d
  ago") below a week, then the day and month, with the year when it is not this year. A row opens its quote, its reference a link of its
  own; on hover or focus it offers Open, Duplicate, and Archive or Restore, each reloading,
  once answered, the list showing then, whichever list the underwriter has moved to. The list,
  stage, search, assignee and order are in the page's address, so Back returns to the
  list as it was. Loading shows placeholder rows, a failure says why with Retry, and an
  empty list says why: no quotes yet, with New quote; none matching, with Clear search;
  none archived.
- **A quote in the app.** A quote opens at `/quotes/{reference}/{option}` on the sheets,
  under its bar: back to Quotes, its reference and option, its name (its
  `quote_details.quote_name`, typed in the bar as on the sheets and saved with the next
  Save), its stage, Draft or Final, and, when archived, Archived, who it is with (reassigned
  from the people quotes are with, or a name typed), Save, Mark final or, for a final quote,
  Amend, which asks why, a menu of Duplicate, Renew, Create alternative, Archive or Restore
  and Copy link, History, and why the quote has no price. Beneath are who saved it and when,
  the release that priced it and the quote it came from. The quote is priced as it is
  typed, and as it opens while it may change and has no price of this release, unpriced or
  priced on an earlier one. Save waits for that pricing to settle, a value entered while it
  waits priced too, and keeps the quote priced when the values settle on a price, and
  unpriced when they could not be priced, which its label ("Save unpriced") and title say;
  with nothing typed, the details the app sets and the defaults aside, there is nothing to
  save, and nothing new to save while the values are as saved and priced as saved on this
  release (a price made since, or one of an earlier release, is new). A save holding a value
  not of its column's type is refused, naming each, its values kept on the sheets unsaved.
  Mark final is off while the quote has unsaved values or no price of this release, and is
  refused naming each required field without a value. A save and a change name the version
  the values were opened or saved at, which a note does not move, so a save over a quote a
  colleague moved on meanwhile is refused rather than written over theirs; an answer
  arriving once another quote is open, or the quote was left, is that quote's no longer and
  is dropped, and a quote being opened takes nothing typed and saves nothing until it is
  read. A question asked about a quote (Mark final, Amend, a name for who it is with) is
  dropped as the page moves to another quote or is left, and a change only ever changes the
  quote it was asked about, refused naming it once another is open. A save is written once
  whatever is lost on the way: its operation key stays bound to what it sent, and a save
  whose answer never came, or came as a server failure (a status of 500 or more), is kept as
  it was sent, in this browser's draft too, so the next Save repeats it exactly before
  saving anything typed since; only a refusal (a status below 500) lets it go, and a repeat
  that landed and was changed since by someone else is let go as done, the page offering
  the newest. While a save or a change is being made, Save and every control that changes
  the quote are off. The cells of the columns the app sets take no typing; assigning,
  archiving, restoring, marking final and amending each change the quote's details, its
  values and prices kept, which the sheets take on, what was typed kept. A new quote is
  keyed in a window before it is made. New quote opens it over the Quotes page, its list as
  it was; Duplicate and Renew over the quote or the Quotes page they are pressed on; and
  Save as new, after a save conflict, over the quote, its unsaved values kept. The window
  holds the quote's reference and the other columns of `quote_details` the underwriter types
  (`quote_name`, `insured`, `broker`, `inception_date`, `expiry_date` and `is_renewal`) in
  the table's order, each drawn as a Collection draws its column, every one shown and
  checked whatever the sheets show or hide: the reference required and fitting its rules,
  and every value fitting its column, a required one left empty for Mark final to ask for;
  Create waits on them, and Cancel or `Escape` closes a window that has sent nothing,
  leaving the page behind it as it was. New quote's starts as a new quote's defaults;
  Duplicate's and Renew's are filled from the source as it is kept, without the details the
  app set for it, its reference blank, a renewal with `is_renewal` ticked, which the
  underwriter can untick, as one not made by Renew can be ticked by hand; Save as new's from
  the values on the sheets. A reference is trimmed and kept as keyed, in Unicode's composed
  form, 1 to 40 characters, no `/`, which the app puts before the option, no control
  character, and not `.` or `..`; two are one reference only when they are the same, case
  included, so `ABC-1` and `abc-1` are two. Create makes the quote from what the window holds
  over a new quote's defaults, the source's values or the sheets' values, under the reference
  keyed as option 1, and moves to its own address; a reference in use is refused naming the
  quote holding it, with Open it. The whole creation is kept in the tab before it is sent:
  while unanswered the window takes no typing; a refusal lets it go and opens the window for
  typing again; an answer lost, or a failure of the server, offers Try again, which sends
  the creation as it was, and Close, which keeps it, and a kept creation is offered back
  with Try again and Discard the next time the app opens in the tab, so a quote is made once
  and nothing typed is lost. `/quotes/new` is the Quotes page with the window open,
  `/quotes/new?from=…&option=…&as=` `duplicate` or `renewal` the Quotes page with the window
  filled from that quote. Create alternative is made at once from the source's values under
  the source's reference as its next option (`as=alternative`), its creation's operation key
  held in the tab until the answer comes, so a reload before then asks with it again and it
  is made once. A quote made from another names what it was made from and any row of it that
  no longer fits. Made, it is clean: left as it opened it keeps no draft and asks nothing on
  leaving, and one left with nothing typed stays a draft; an answer arriving once the page
  moved on does not move it, nor does leaving a quote whose unsaved values the leave guard
  keeps, and a quote made where the page did not move says so, "Made" and its label; one who
  may only read quotes makes none. The sheets take no typing and are not priced, and Save is
  off, while the quote is final or archived. A save the server priced differently from the
  sheets says so and shows the server's price. A save or change over a quote moved on since
  it was opened names who moved it, keeps the underwriter's values, and offers to open the
  newest or to save the values as a new quote, keyed in the window filled from them, made
  unpriced as Duplicate makes one and priced as it opens; a page drawn for an earlier
  release says so and offers to reload. Values not yet saved are kept in the browser as they
  are typed, at once as the quote is left, and again after a save for what was typed during
  it, and offered back when the quote is opened again, or restored or amended, as not saved
  to the server, until they are saved or discarded; only a quote that may change offers
  them, and they are put back on its sheets' columns alone, the details the app sets as the
  quote has them. Leaving a quote with unsaved values asks first, Back and Forward too, the
  history's entries kept as they were, and closing the tab warns. Archiving says so with
  Undo, which restores the quote archived whichever is open by then. History, on the right,
  holds the handover note, edited there, the quotes it came from or that were made from it,
  and what was done to the quote, by whom and when, newest first, with Amend's reasons.
  Ctrl+S saves.
  Without a quote store the app opens on a new quote, priced as it is typed, and says it
  keeps no quotes. One who may only read quotes is offered nothing that changes them: no
  New quote, Duplicate, Archive or Restore on the Quotes page, and every quote read only,
  saying that changing it needs the app's edit role.
- **Shown when.** A sheet, a section, a component, or a Table's column or a Collection's
  field, with conditions shows only while they
  hold on the values typed into the sheets: a new quote's defaults alone while building, and in
  Preview the sample or the underwriter app's quote; a section on a sheet that does not
  show does not show either, nor does a
  component in a section that does not show, nor a field of a component that does not show.
  - **A condition holds** while its column is in a one-row input table and on a Table or a
    Collection that shows it, the component showing and so does its field, and the value
    typed into it passes the test, read as the server types it:
    text without the spaces around it, compared exactly; a tick box ticked or not, one
    never touched unticked; a number read as the sample's numbers are (a currency sign,
    separators and spaces allowed) and compared as a number; a date as the day it names;
    and a cell blank or untouched as empty. Text within text compares as equals does:
    `contains`, `starts_with` and `ends_with` pass a value holding the text tested for,
    at its start or at its end, exactly, without the spaces around either. Of the tests
    other than the ticks, an empty value passes `empty`, `not_equals`, `not_contains`
    and `not_one_of` alone, and a value not of its column's type (text in a number
    column, or a tick left in a column since retyped) `filled`, `not_equals`,
    `not_contains` and `not_one_of` alone. A test against another field holds only while
    that field shows too, as its own column must, so hiding still only hides more, and
    reads the other field's value as it reads its own: one in a one-row table's first
    row, or, in a test of a row, the same row's. A value there that is not one of its
    type, blank among them, gives nothing to test against, which `not_equals` and
    `not_contains` alone pass. A Date against another Date compares the day with that
    day and its days added. A condition on an output column holds here whatever the price
    (On the price, below). A condition that cannot hold as written
    never holds: one naming a column that is not in a one-row table (gone, or a many-row
    table's), a condition on rows naming a table that is not a many-row table, a test of a
    row naming a column that is not its table's or is its index, a condition on an output
    tested against another field, a total of a column that is not a number column of its table other than its
    index or against a value that is not a number, a test its column's type does not take, a number of
    values its test does not take (none for the ticks, `filled` and `empty`, at least one
    for `one_of` and `not_one_of`, else one), or a value its column could not hold (blank,
    not a number, a fraction for an Integer, not a `YYYY-MM-DD` day); a test against
    another field that takes no value or several, that has values typed too, whose field
    is not a column of a one-row input table (in a test of a row, of its table other than
    its index) or not of its column's type; and days added but to another Date field.
  - **On a list.** A condition on a many-row input table's rows holds while each column it
    tests, and a total's column, is on a Table that shows it (for a count without tests,
    one of its table's columns other than the index), and its rows pass: any of the table's rows
    passing all of its tests, every one of them passing them all and there being at least
    one, the number of rows passing them all by its test, compared as whole numbers, or the
    total of its column's numbers in those rows by its test, compared as numbers to 15
    significant digits (so 0.1, 0.2 and 0.3 sum to 0.6), each row's value read as a one-row
    table's is. A sum of no numbers is 0; a least, greatest or
    average of none is empty and fails every test; a cell that is blank or not a number adds
    nothing to a total. A
    table's rows are those a grid showing it holds (a column giving it a row for each
    value gives every one of those) with anything but the index typed in them, whatever
    the sheets hide: a row with nothing typed in it is no row, as the quote leaves it out.
  - **At least some.** A `show_when` or a group whose `match` is a number holds while at
    least that many of its conditions hold, a group counting as one: a section that shows
    while two or three of three cover boxes are ticked needs at least 2 of the three
    boxes' conditions. Asking for more than it holds, it never holds.
  - **Groups.** A group holds while all of its conditions hold, any, or at least a number
    of them, as its `match` says, and counts as one of its `show_when`'s conditions: a
    section that shows while the cover is Comprehensive and either a driver is under 25 or
    the claims count is at least 2 needs all of the cover's condition and a group needing
    any of the other two.
  - **On the price.** A condition on an output column, or on an output table's rows, tests
    the price the sheets show, in the Show when of what takes nothing typed: a Heading, a
    Chart, a Collection or a Table of output columns alone, a field of an output column,
    or a section or a sheet holding only those. What shows is decided from the inputs
    first, such conditions holding, so the price never decides what is priced; they are
    then applied to the price the sheets show, the last one while a new one is on its way,
    and, before the first and after a pricing that fails, every output read as empty,
    hiding what holds them as any condition hides. An output's value is tested as a typed
    one is (a number as a number, a tick as a tick), a many-row output table's rows as a
    list's, against values typed alone, never another field, whether or not a component
    that shows has the output. A Table of referral reasons shown while `referral_count` is
    above 0 is hidden until a price gives a count above 0, and again once one gives none.
    The server, which decides what a quote leaves out, holds them all, as they change
    nothing there. One on what takes values typed, or on a section or a sheet holding such
    a component, a Collection of input fields or a section holding one placed there later,
    is a problem the view names, and is never applied: hiding by the price there would hide
    values still priced.
  - **Fields.** A Table's column or a Collection's field with a Show when of its own hides
    by it as a component does, within its component: a Collection's fields after it close
    up, flowing on through its columns, a spacer going with the fields after it, up to the
    next spacer, while it has some and all of them hide; a Table leaves the column out of
    its grid, and a transposed Table the field's line. Its conditions may test its
    component's other fields, which is what they are for: the earthquake fields of a
    Collection shown while its earthquake box is ticked. A field that its own value alone
    shows could never show again once hidden, as a section's own tick box inside it could
    not.
  - **Decided at once.** What shows is decided for the whole form together: everything
    starts shown and anything whose conditions fail is hidden, which may hide a column another
    condition tests, until nothing more hides. So a sheet that shows when a box on a
    hidden sheet is ticked is hidden too, whatever the box holds, as is a section that
    shows when a box in a hidden section is ticked, and the order of the sheets, sections,
    components and fields decides nothing.
  - **A quote is what the sheets show.** A value typed into an input column that is on
    Tables or Collections, none of which shows it, is left out of the quote, as if it had never
    been typed: a row with nothing typed in it but such values is left out as an empty row
    is; the index, which nothing is typed into, is never left out; a column on no Table
    or Collection is not left out; and nor is a column of `quote_details` the app sets,
    which every request holds, so a sample holding only such a value counts as typed in
    Preview, though in the app, where they are locked, it does not. A Chart takes nothing typed, so the columns it plots
    count for nothing here: a value only hidden components take is left out however a Chart
    that shows plots it, and a condition on a column only a Chart plots never holds. A
    Heading shows no columns, so it counts for nothing here either, while it hides and
    shows by its own conditions, its section's and its sheet's as any component does. The
    values are kept, not cleared: they stay in the sample and the
    quote as typed, the sample saved with them, and are in the quote again once their
    column shows. The server leaves them out where it makes a quote of the form's values,
    so the sample priced in Preview, the sample a Workbench Input previews on and the
    app's quote leave out the same values.
  - **While building** a new quote decides, its defaults alone typed: a hidden sheet's tab, a hidden section, a
    hidden component and a hidden field are drawn faded, still shown, selected and edited
    like any other, the sheet's sections and a component's fields keeping their places;
    the sheet being built is decided as if it showed, so its own conditions fade nothing on
    it, its tab alone marking it hidden, and what is on it fades for its own conditions; and
    a sheet, a section, a component or a field with conditions carries an eye (a section's
    in its bar, a field's after its label, or a column's in its header), struck through
    while it is hidden, that names its conditions on hover.
  - **In Preview** the values decide as they are typed, the sample or the app's quote: a hidden sheet has no tab; a hidden
    section is not drawn and the sections under it move up to close the space it took;
    a hidden component is not drawn, its place in its section left empty, since the
    components are placed freely in it; and a hidden field is not drawn, its component
    closing up around it (Fields, above). While the sheet chosen is hidden, the first sheet
    that shows is shown in its place, and while none shows the view says "No sheet shows
    for this quote." Fitting takes the components that show. While a pointer's primary
    button is pressed on the sheet, what shows and where, the sheet showing included, stays
    as it was when it was pressed, and changes once the press ends (released, cancelled, or
    taken by a context menu or another window), so a value committed by the press (a text
    cell left for a tick box) never moves the press's target from under it. A press on a
    dropdown holds nothing: its menu opens where it is and takes the release. When what shows
    changes above the section holding the focus, or else above the first section reaching
    into view, the sheet scrolls by as much as that section moved, so the underwriter's
    place stays where it was; nothing scrolls when that section itself closes.
  - **Problems.** The view names, on the dashed frame of a component, on a section's bar
    and in the warning colour on a sheet's tab, on hover and to a screen reader, what stops
    a sheet's, a section's, a component's or a field's conditions doing what they say, a
    field's on its component's frame naming the field: a condition
    that cannot hold as written, saying why; a value that is not one of its column's
    allowed values; a `show_when` or a group asking for at least more conditions than it
    holds; and a sheet, section, component or field that nothing could show again once
    hidden, since each column its conditions need (for a count without tests, each of its
    table's columns but the index) is on no Table or Collection, or only on
    itself, on its own components or on components or fields that cannot show without it,
    as a tick box in the very section it shows is. None is refused: the form is saved as it is.
- **Saved.** A successful save of the form adopts the folder's new revision; the project
  save then fetches the tables afresh, so the Workbench Input's and Workbench Output's
  ports follow the schema, as after any fetch, and saves the pipeline. A save refused
  as stale keeps the edits and says so, in a toast and in a banner in the view whose
  Reload reads the folder again, dropping the edits and the history.
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
  number, whole digits in an Integer column read exactly however large, an unticked box in
  a filled row is false, and a value that is not of its column's type stays as typed, for
  the Workbench Input to report.
- **The Workbench Input.** A pipeline takes the tables through the Workbench Input:
  `workbenchInput` in the pipeline document, declared in the pipeline file with
  `@pipeline.workbench_input(config=...)` (a submodel's file uses `@submodel.workbench_input`)
  and configured in `config/workbench_input/<function>.json`, which holds neither its tables
  nor its sample. In the editor it is "Workbench Input", badged "WORKBENCH IN", in the entry
  group with the Quote Input's colour and shape and an icon of its own. Its tables are the
  form's input tables and its sample the form's sample quote (The tables as the pipeline
  holds them), read from the form folder wherever the pipeline runs (below).
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
    the input does), so a deployed Workbench Input gives each port its table, as a
    deployed Quote Input with tables does ([deploy](../deploy/high-level.md)).
    The memory estimate of a deployed run sizes each consumer from the table that feeds it,
    not from the one-row request that holds them all. None of them reads the sample. Haute calls the two the request inputs and decides this
    once, from one set holding both types; tests fail when a check in the code names the Quote
    Input alone where it means a request input.
  - **The quote's identity.** A request and a frame differ in shape. A request's tables
    hold their own columns, and the quote's reference and option are named in its
    `quote_details` alone: a request without them, with an option below 1, or giving either
    in another table, is refused naming the spot. Reading it puts them on every row of every
    table leading with them. Every quote has them: the sample, read in editor runs, previews
    and `run()`, the null quote, which an empty sample and deploy's dry run read, and the one
    quote of an underwriter app without a quote store are priced under the stand-in,
    `PREVIEW` and option 1, which the form never keeps; deploy's request schema holds them
    in `quote_details` alone. The app's own requests hold the quote's, whatever its sheets
    hide, as they hold every column of `quote_details` the app sets. Those others a request
    may leave out, null then: a deploy caller's values are priced as sent, the null quote
    holds them null, the sample holds what the analyst typed, and an app without a quote
    store drops whatever its page sends for them.
  - **No data file; the sample.** A Workbench Input reads no data file. Without a request, in editor
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
    types, and the dry run reads one quote with nothing filled in, one row of nulls per table
    under the stand-in identity, the rows a preview runs on before the workbench supplies
    values. A node that cannot take a
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
    dragged from it has the tables fetched as its ports at once, or none until the first
    fetch finishes. Enabling or disabling the workbench changes only what the palette
    offers: a pipeline keeps the request input it has, and a Quote Input becomes a Workbench
    Input, or back, only by deleting it and dragging in the other.
  - **Read from the form folder.** Wherever the pipeline runs from its source, a Workbench
    Input's tables and sample are those the form folder of its project defines, as
    `GET /api/workbench/tables` answers them, the tables checked as the pipeline takes them:
    in the editor's previews, runs and traces, each request reading the folder anew, so an
    edit made outside the view reaches the next one (Preview's pricing gives the form the
    view holds instead, above); in the pipeline file's own `run()` and `score()`, from the
    project the file is in, `score()`, which gives the Workbench Input the frames it is
    given, still reading its config file and the form and refusing a connection to a table
    the form does not have; and in `haute deploy` and `haute app build`, which read the
    folder once and carry the tables in the bundle, the sample left out (No data file;
    the sample, below), so a deployed pipeline and a release run on the tables they were built
    with and never read a folder. A Workbench Input inside a submodel reads the same form.
    Its config holds neither: `tables` or `sample` in it is refused (Failure model).
  - **Shown in the editor.** The editor fetches the tables, with the sample, when it finds
    the workbench enabled, each time it adopts a pipeline document (opening one, or reloading
    it after a branch switch or a change on disk), whenever the form folder changes on disk
    and after each save of the form; only the newest fetch counts. A Workbench Input's ports
    are the tables fetched, at the top level and inside a submodel alike. Nothing of them is
    in the node, so an edit of the graph, an undo and a redo never change them, and a fetch
    never changes a Quote Input.
  - **Connections follow table names.** A connection to a table whose name the tables
    fetched still have stays, however the tables were reordered or replaced; one to a table
    whose name has gone is removed and reported, as removals are for any request input edit,
    once the tables are fetched, in the graph the editor shows while the document can
    change, as a change of its own outside the undo history, and again whenever an undo or a
    redo brings one back, since the tables are the form's and not the history's. A renamed
    table is a new port. A connection to a table the tables do not have, as one written into
    the pipeline file by hand can be, fails the run naming the table. A Quote Input's
    connections still follow a renamed table by position.
  - **Its panel** shows the tables fetched, read-only, under "Tables from the workbench",
    with whatever the editor finds wrong with a table's name as a port (not an identifier, a
    reserved word, a repeat), and a note on what previews run on: "Previews run on the
    workbench's sample values, read as a request is: a table with many rows and nothing
    typed has none." while the sample has values, and "Previews run on one row of nulls per
    table until the workbench supplies values." while it has none. Saving the pipeline
    refuses a connection from a table named like another node, which the saved file could
    not be read back with. The Quote Input's panel is its table editor, whatever the
    project enables.
  - **The assistant** reads a Workbench Input and connects nodes to its tables, or removes
    such connections, as it does a Quote Input's, but cannot add, change, rename or delete one,
    nor edit its steps: its node card says the tables are the workbench's and the analyst adds
    the node from the palette.
- **The Workbench Output.** A pipeline fills the output tables through the Workbench Output:
  `workbenchOutput` in the pipeline document, declared in the pipeline file with
  `@pipeline.workbench_output(config=...)` (a submodel's file uses `@submodel.workbench_output`)
  and configured in `config/workbench_output/<function>.json`, which holds none of its
  tables: they are the form's output tables, read from the form folder as a Workbench
  Input's tables are. In the editor it is "Workbench Output", badged "WORKBENCH OUT", in the
  exit group with the Quote Response's colour and shape and an icon of its own.
  - **A port per table.** A Workbench Output has one input port per table with a column,
    named by the table, in the tables' order, and nothing downstream. A connection lands on
    one table's port: its `targetHandle` is the table's name, and the pipeline file says
    `pipeline.connect("<node>", "<workbench output>", target_port="<table>")`. A table takes
    one connection, and a node fills one table: its frame reaches the Workbench Output under
    the node's own name, as it reaches any node, so a second connection from the same node is
    refused, saying which table that node already fills.
  - **Filled by name.** Each table column is filled from the column of exactly its name, case
    included, in the frame connected to its table's port; nothing else of the frame is taken
    and no column is filled from another of a different name. A column the frame lacks, one
    whose type does not fit its declared type (below) and every column of a table nothing is
    connected to are filled with nulls, and the panel flags each, so the pipeline runs while
    it is being built: a one-row table nothing is connected to holds one row of nulls and a
    many-row one no rows. None of them stops a run. A table leading with the quote's
    reference and option holds them null in the node's tables whatever its frame holds, and
    the panel never flags them; the response fills them.
  - **Its tables are its result.** When the node runs, its result is its tables, one frame per
    table with a column, under the table's name, each holding the table's columns in the tables' order with
    their declared types, and nothing else of the frames it was given. A column fits its
    declared type when it is of that type or null throughout, when it is any integer for an
    Integer column or any number for a Decimal one, and when it is categorical for a Text one.
    A one-row table holds exactly one row; a many-row table holds any number, in the order
    its frame delivers them, so a step upstream that leaves row order to Polars can list
    them differently from one run to the next. Clicking the node
    previews its tables as dataframes, one at a time, chosen from the preview's table picker
    when there are several, the first table first. Clicking a value in them traces it from
    the node connected to its table: the same row of that node's frame, and its column of the
    value's column's name. A value filled with nulls, its column missing from the frame or of
    a type that does not fit, its table not connected, or the quote's reference and option
    the table holds blank, has no value to trace, and tracing it says so.
  - **The response.** The tables are what a priced quote's output tables are filled with. Where
    the pipeline answers a request, its tables become the response for one quote: a list of one
    object holding each one-row table as an object under its name and each many-row table as a
    list of objects under its name, each column under its name, as a quote holds its tables.
    The response is the tables as they are, every row and every column, save that every row
    of a table leading with the quote's reference and option takes the quote's, read from
    the run's `quote_details` frame where the response is built (the request's in deployed
    scoring, the source's in `Pipeline.run`, the frame given to `Pipeline.score`, null
    without one), so a pipeline need not carry them through; where it is rendered as JSON a
    null value or an empty list is left out, as a Quote Response's are.
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
    Output dragged from it has the response tables fetched as its ports at once, or none
    until the first fetch finishes. Enabling or disabling the workbench changes only what
    the palette offers: a pipeline keeps the response node it has.
  - **Shown in the editor.** The fetch that gives each Workbench Input its ports gives each
    Workbench Output its tables, at the same moments and in the same way: its ports are the
    response tables fetched, at the top level and inside a submodel alike, nothing of them
    is in the node, and a fetch never changes a Quote Response.
  - **Connections follow table names.** A connection to a table whose name the tables
    fetched still have stays, wherever the table has moved; one to a table whose name has
    gone is removed and reported, as for a Workbench Input. A renamed table is a new port.
  - **Its panel** shows each table: its name, whether it has one row per quote or many, the
    node connected to it or "Not connected", and whatever the editor finds wrong with its name
    as a port; then each of its columns, its name and type in a box of its own. The box's
    border is red while the column is missing (the connected frame has no column of its name,
    or nothing is connected to the table) and orange while the frame's column is of a type
    that does not fit, judged by the columns and types the frame's last preview recorded; a
    connected frame not previewed yet is judged by neither. The panel changes nothing: the
    tables themselves are read-only, under the Workbench Input panel's title.
  - **The assistant** reads a Workbench Output and connects nodes to its tables, or removes
    such connections, but cannot add, change, rename or delete one: its node card says the
    tables are the workbench's and the analyst adds the node from the palette.
- **The quote store.** The quotes the underwriter app keeps are rows of the form's own tables
  in a database, saved and read with plain SQL: a SQLite file on the analyst's machine,
  dev's, which `haute app serve` keeps, and, hosted, the database the app is given. Each
  table of the form's schema is a table of the same name: `quote_details`, every input table
  and every output table, its columns the schema's under their names and in their types, an
  Integer a 64-bit integer, a Decimal a double, Text unbounded text, True/false a boolean and
  a Date a date. Every column of the project's own is nullable, so a draft saves with any of
  them empty; Required is checked as a quote is marked final, never by the database. Every
  row holds the quote's identity, `quote_reference` and `quote_option`, with which every
  table but `quote_details` leads, and a row of a many-row table holds `quote_row` too,
  Haute's number for it: an input row's place on its sheet, blank rows counted, so a grid
  opens as it was saved, and an output row's place in the pricing's answer. The identity,
  with `quote_row` in a many-row table, is each table's primary key, and each table's
  identity refers to its quote's `quote_details` row. A one-row table holds one row for each
  quote, null where nothing was typed. A report joins the tables on the identity:

  ```sql
  SELECT d.broker, SUM(p.total_premium) AS premium
  FROM quote_details AS d
  JOIN premium AS p ON p.quote_reference = d.quote_reference
                   AND p.quote_option = d.quote_option
  WHERE d.stage = 'final'
  GROUP BY d.broker;
  ```

  Beside them Haute keeps two tables of its own. `haute_store` holds one row: the project,
  the environment, the release and Haute version it was made for and when, and the
  structure of the form's tables it holds, each table's and column's stable id with its
  name, type, role, rows and whether it is the index. `haute_activity` grows by a row for
  each thing done to a quote (made, saved, assigned, archived, restored, marked final,
  amended, noted), holding who did it and when, the quote's version after it, its operation
  key, and the reason, note, assignee or pricing release it carried; nothing changes or
  removes a row. Haute writes no other table. `quote_details` also holds the store's own
  columns, which the schema editor shows locked beside the form's and no request or frame
  holds: `quote_version`, which every write but a note moves on; `saved_at` and `saved_by`,
  when the newest save was made, in UTC, and by whom; `priced_release`, the release whose
  pricing the output tables hold, null while the quote is unpriced; `source_reference`,
  `source_option` and `source_relation`, the quote this one was made from and how (a
  duplicate, a renewal or an alternative); and the note as it stands, `handover_note` with
  `note_by` and `note_at`, so a quote's note is read whole. A many-row table's `quote_row`
  is the store's own too.

  The details the app changes over a quote's life, `stage`, `assignee`, `archived`,
  `modified_date`, `modified_by` and `haute_version`, are columns of `quote_details` that no
  request holds, so changing them never leaves a stored price its stored inputs no longer
  give; `quote_reference`, `quote_option`, `created_date`, `created_by` and the details the
  underwriter types do. Every column of `quote_details` is stored, and each that reaches the
  pipeline is priced, whatever the sheets hide; any other cell a Show when hides is saved
  blank, as the request leaves it out, so the input tables hold what the quote was priced on.

  A save is priced first, then written in one transaction: the quote's `quote_details` row
  updated where its `quote_version` is the one the page opened or saved it at and the quote
  is a draft not archived, the version moved on, when and by whom it was saved, the release
  that priced it and the administration set; then, in each other table, the quote's rows
  deleted and its new rows inserted, an output table's from the pricing, each value that is
  not finite empty, and none when the quote is saved unpriced; then its activity row. Rows
  are written from typed values, never from the copies made for JSON, so an integer past
  2^53 is kept exactly, and the clock is read once, before the pricing, so the priced
  request and the rows hold one instant. A save is refused, with nothing written, when a
  value is not of its column's type, naming each table, row (as the sheet numbers it) and
  column, before anything is priced; otherwise only when the quote has moved on since the
  page read it, naming who moved it, what they did and when, when it is final or archived,
  for one who may only read quotes, and when nothing is typed. Nothing else a quote's cells
  or outputs hold refuses a save: a required input or output left empty, a value outside its
  range or allowed values, and a non-finite output all save. A quote's name (`quote_name`)
  is at most 200 characters, a longer one refused naming the limit.

  A quote is `draft` or `final`. Mark final makes a draft final once it is priced on the
  running release and every input and output column marked Required holds a value, and is
  refused naming each that does not: a required column holds a value when, in a one-row
  table, its cell does, and in a many-row table, every row's does, a table without rows
  passing; text of spaces alone is empty, zero and false are values and null is not (an
  unticked box in a filled row is false; a True/false output left unfilled is null). A
  final quote takes no save; Amend takes it back to draft, giving a reason, and its next
  save overwrites what was offered. Assigning, archiving and restoring work in both stages
  and change the quote's details alone, its values and prices kept; an archived quote takes
  no change but being restored. Each change is one compare-and-swap on the quote's row over
  the version it names, refused with nothing written when the quote has moved on, its
  activity row written in the same transaction. A note moves no version, so it never
  conflicts with another write, and is refused on an archived quote.

  Each creation and save carries an operation key, bound to who sent it, the quote and what
  it does, and the first write under a key is the one that counts. One repeated under its
  key, as the page repeats one whose answer was lost, answers what the first did, before
  the release it was drawn for is checked, with nothing priced or written again: a
  creation the quote it made as it stands, a save the quote while that save is still its
  newest change, and otherwise a refusal of its own (`write_superseded`) saying when the
  save landed and who changed the quote after it, which the page takes as done; two sent at
  once under one key are one write. The key of another person, another quote or another
  kind of write is refused.

  A new quote, a duplicate and a renewal take the reference the underwriter keys, and option
  1; an alternative takes its source's reference and the next option, its source's option 1
  held while the highest option is read, so two made at once take two. A reference is
  trimmed, 1 to 40 characters, holds no `/` or control or format character, and is not `.`
  or `..`; it is kept in Unicode's composed form (NFC), and two references are one only when
  they are the same, case included, so `ABC-1` and `abc-1` are two quotes. A reference
  another quote holds is refused naming it. A quote is made unpriced, and priced as it
  opens.

  A quote opens from its rows, read together and read again when the quote moved on while
  they were read: each typed value as the cell text it reads as (an Integer its digits, a
  Decimal the shortest text that reads back as it, a Date its ISO text, True/false a tick or
  none, and null a blank), a many-row table's rows in their places by `quote_row`, a gap a
  blank row, and its outputs as the pricing answered them. A value comes back as its value,
  so `£1,000` comes back as `1000`, which its column's format shows. A preset row whose
  value is no longer allowed is named as dropped. The app's lists read `quote_details`
  alone, a page at a time: the quotes assigned to the signed-in underwriter, the team's or
  the archive, of one stage when asked, matching every word searched for against the
  reference, quote name, insured or broker whatever their case (`ABC-1/2` matching that
  option alone), newest saved first, by reference from A to Z with its options in order, or
  by insured, with each list's count and each stage's.

  A project's quotes live in dev, on the analyst's laptop, and in the hosted environments its
  `haute.toml` declares, each a `[workbench.store.<name>]` table naming its database's
  provider: `sqlite`, a file on the host, which takes no sign-in. Dev is the SQLite file
  `.haute/app/store.sqlite` that `haute app serve` keeps; no project declares dev, and nothing
  points a laptop at a hosted environment. A host or a CI job picks an environment with
  `HAUTE_ENVIRONMENT`, and the host's own environment says where its database is:
  `HAUTE_STORE_DATABASE`, the file's path, or `HAUTE_STORE_URL`, a whole connection URL, never
  both. No credential, server or database name is in a file the project versions: a table
  naming where a database is, or holding a secret's value, is refused naming the variable it
  belongs in, so a pull request cannot point an environment elsewhere. A release carries the
  declarations with it, so a hosted app and a migrate read them from the release they run,
  never from a project checkout, and an environment the release does not declare is refused
  naming those it does. `haute_store` records the project and the environment, so an app
  pointed at another's database refuses to start naming both.

  The store holds the tables of one form, as `haute_store` records them by stable id, and
  one planner brings a database to a release's form, in one transaction, refusing before any
  DDL a step it cannot take: `haute app serve` runs it on dev's store as it starts, in dev's
  mode, and `haute app migrate` on a hosted environment's before a release starts. It makes a
  table the form gains, gives a table a nullable column the form gains, renames a table or
  column the form renamed, in two steps through temporary names so a change of case or a swap
  of two names works, and changes the type, rows or role of a column or table that holds no
  value, replacing the empty column or making the empty table afresh, which loses nothing.
  Hosted, it drops nothing the form removed and nothing that holds a value: a column or table
  the form dropped is kept, unused, and every plan lists it with how many quotes hold values
  in it and the DROP statement for the DBA; a migrate first compares its record with the
  database, so a kept one the SQL side has dropped is forgotten and its name is free, while a
  table or column the form still has that the database lacks is refused, naming it. A save
  writes only the form's tables: a kept column of one of them is left empty for each quote
  saved since, and a kept table, which no save touches, keeps every quote's rows as they
  stood, and they come back with it if its id returns. Hosted, it refuses a type, rows or role
  change on a column or table holding values, naming it, how many quotes hold values and the
  way forward (a new column or table, or emptying it first), and a new column or table taking
  a kept one's name. In dev it drops what the form removed and builds again what changed
  while holding values, naming each table whose quotes' rows went and each column whose
  values went, and builds the whole store afresh when an earlier Haute made it with other
  columns of its own, which a hosted migrate refuses. `required` never becomes NOT NULL. Every plan says, for
  each step, whether a migrate back to the previous release reverses it or only a restore does
  (a change to an empty column or table that the release may then fill); a hosted plan
  holding one is applied only given `--restore-point NAME`, the backup or point in time IT can
  return the database to, which `haute_store` records, and dev asks for none. `haute app
  migrate --plan` prints the plan for the environment's database without changing it;
  `--plan --base DIR` prints it offline, from the form of the release built in `DIR`, for a
  pull request, headed as provisional, each step that depends on the data shown as
  conditional and a removed one shown kept with its DROP; and `--sql` prints the plan as the
  SQL a DBA runs instead, `haute_store`'s row last.

  A database of an earlier Haute's store, of another project or
  environment, or holding a table of the form's name the store did not make, is refused,
  nothing adopted, and the app does not start on a database that does not hold exactly its
  release's form's tables, as `haute_store` records them and as the database's own
  catalogue holds them: every table and column the release uses there, of its kind, taking
  a null as the store's does, keyed and referring to its quote as the store keys it, and
  every other column of those tables taking a null. It refuses to start naming each
  difference and what resolves it. Every table and column name is quoted in the SQL Haute writes, so
  a reserved word (`order`, `group`) is a name like any other; refused wherever the form is
  saved, naming each, are two columns of one table whose names differ only in case, a name
  longer than 63 characters, a name starting `haute_` (Haute's own `haute_version` aside), a
  column of a many-row table named `quote_row` and a table named starting `sqlite_`. A
  SQLite store keeps foreign keys on and its rollback journal, never write-ahead logging, so
  the file stands alone between writes; its DDL runs in the transaction it is part of, and
  it needs SQLite 3.35 or later.

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
- **The form is a folder in the pipeline folder.** The pipeline's quote tables come from the
  form and the underwriter app ships it with the pipeline, so the form belongs with the
  pipeline, in its history, where a reviewer sees the schema change that changed the
  pipeline's ports. In the pipeline folder it is no special case: the seed, Commit's sweep
  of new files, the release and the CI triggers already take that folder whole. Haute
  reads it from disk on every request that needs it rather than caching it, so a hand edit
  and a branch switch each reach the next fetch without a restart or a stale copy.
- **Named files, as the pipeline's are.** The pipeline is a file of nodes and a settings
  file per node, named by the node; the form is an index and a file per table and per
  sheet, named by the table or the sheet, so a pull request's list of files says what
  changed and a commit message can name it. Finer files, a section's or a component's,
  would be named by ids, since sections carry the builder's titles ("Section 1") and many
  components none; one file puts every analyst's edits in one place.
- **Names in the files, ids beneath them.** A reviewer reads `br_rating.aop_deductible`
  where the form holds `c_2237483f`, and a search finds every use of a column by its name.
  Saved quotes keep values by id, so a table or column keeps a fixed id beside its name: a
  rename, in the editor or by hand, leaves it, and a column made again under an old name
  takes none of the old one's values. A reference to a name the schema does not hold is
  kept, shown and refused before anything is priced, never matched to another column:
  after a merge, the analyst says what it meant.
- **Only what is set.** A column at its defaults is a name and a type; written out in full
  it is twenty lines nobody chose, and a reviewer skimming defaults misses the one setting
  that changed. What a file holds is what someone set.
- **Layout apart.** Where components sit is what the editor changes most and a reviewer
  reads least, so it has a file of its own beside each sheet's: a pull request that only
  moves things lists only layout files, and a sheet file's history is a history of what
  the sheet shows. How components look stays with them, read in context and changed on
  purpose. Ids that start with their kind say what a layout line places, and their random
  ends keep two branches that each add a component to one sheet from giving two
  components one id.
- **A missing folder is a loss, not a new form.** A form does not go missing in the
  ordinary course of work; when it does, version history holds it, and a blank form in its
  place would invite a save that commits the loss. A new workbench comes from
  `haute init --workbench` alone.
- **Read per request, fail where it can be fixed.** The status and tables routes read
  `haute.toml` and the form each time. A bad `[workbench]` table or an invalid form
  answers 409 with what to fix, which the editor shows as a toast, and the pipeline editor goes
  on working; a server that refused to start would hide the pipeline for a typo in the
  workbench's settings.
- **One view host, natively.** The view is shown the way the extension host showed
  Obverse's: the pipeline editor stays mounted, so nothing it holds is lost, and only its
  keyboard and React Flow's document-level keys are fenced. A second React or a shadow root
  bought nothing once the view was Haute's own, so the host returned natively with it.
- **One revision rule.** The form's revision is the content hash of its folder's files,
  each path and its bytes, as the pipeline's manifest hashes its files, and a stale save
  answers the pipeline save's code, so the editor matches one refusal. The bytes are hashed
  rather than the parsed form, so a hand edit that changes only whitespace still counts as
  a change, and so does a file added to the folder or taken out: the view read other files,
  and a save over them must say so. The release and the quote store keep hashing the one
  compiled document they hold, as before, since a release ships one document.
- **The form's files on the ledger.** The form is captured through the pipeline save's own
  capture rather than a capture of its own, so one place decides what a save on the ledger
  means: no working branch, a missing identity, a failed capture. Commit's sweep takes
  tracked files and the pipeline folder's new ones, so a hand edit to the form reaches the
  milestone without the view's help.
- **One Save.** The form and the pipeline are one project on one ledger, so Save, Ctrl/Cmd+S
  and Commit save both, the same from either toolbar, and which view shows never decides
  what a save records. A pipeline saved alone would run on a form the view does not show,
  and its connections would follow tables the folder may no longer hold; one save, the
  form before the pipeline with the tables fetched afresh in between, records a pipeline
  that runs on the form it records and connects only to tables it has. The
  form is saved only when there is something to save, unsaved edits, so a save from the
  pipeline editor costs the form no request and no toast while it is as saved; a form
  file left uncaptured is captured as it is, never written again.
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
  is on the sheet now, as a rater's cell would; every other run reads the saved folder, so
  a save is still what changes the pipeline.
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
  pricing or a save: refusing a quote is for validations, to come. The one exception is the
  new quote's window, whose required columns of `quote_details` are an entry requirement
  of the quote's making alone: an alternative made at once, and every save after, keep a
  quote its rules mark. Clear is gone: it emptied a quote
  at one press, after asking, and could not be undone. Preview's quote is the saved
  sample, so Reset starts it again as a new quote through the form's history instead:
  one undo step, so it asks nothing, and a new quote's defaults rather than nothing, as
  Build draws the sheets and an underwriter's New quote opens.
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
  the sample priced in Preview, the Workbench Input's sample and the app's quote all come
  from, and the view, which hides and shows as each value is typed, applies the same
  rules, the two held to one set of cases both test against.
- **The price decides what shows, never what is priced.** A section of referral reasons
  matters only once the price gives some, but the price is made of what the sheets show:
  letting it hide what takes values would make what is priced depend on its own price. So
  what takes values is decided from the inputs alone, and the price only hides what takes
  nothing typed, after, where hiding changes nothing priced.
- **Decided at once, by hiding.** Conditions test the values as typed, except that a
  condition on a column no component that shows has, or tested against such a field,
  never holds, so hiding only ever hides more: starting from everything shown and hiding until nothing changes gives one answer
  whatever the order of the sheets and components, a chain of conditions (a sheet shown by
  a box on a sheet shown by another box) is followed without being written twice, and no
  value the underwriter cannot see decides what they see.
- **One level of groups, all or any.** A group in the Show when says each mix of all and
  any a sheet asks for, a cover and either of two extras or either of two covers each
  with its own, since a test's opposite stands in for a "not"; a deeper tree would say
  nothing more and be harder to read and to lay out in the panel. Neither takes "none of
  these": a condition fails once its column hides, so "none" could come to hold as hiding
  went on and show something again, and hiding has to only ever hide more.
- **Hiding, never reading a hidden value as empty.** Reading a hidden value as empty in
  the form's dependency order would allow "none of", "at most", "exactly" and "not" over a
  group, but let that order decide what shows; the rounds stay (decided 2026-10-20). Rules
  named once and used by many sheets were offered then and not taken, nor were a Table's
  cells shown row by row through "this row" tests, offered again on 2026-10-22.
- **At least, never at most.** "At least 2 of these" says two or three of three boxes
  without writing out every pair as a group of its own. Neither takes "at most" or
  "exactly", for the reason neither takes "none": hiding a column makes fewer conditions
  hold, so "at most 1" could come to hold as hiding went on.
- **A list's rows are its typed rows.** A condition on a many-row table counts each row
  with anything typed in it, whatever the sheets hide, because counting only what shows
  would let hiding a column drop a row, so that every row left passes or the count falls
  under a limit, and show something again. Only a row typed into a Table that hides, while
  its table is on another that shows, counts without showing, which a table's rows,
  inserted and deleted in every table a grid shows at once, rarely come to. Every row
  needs at least one row, as nothing typed decides nothing: a list nobody has filled
  shows no section waiting on all of it.
- **A row's tests together.** A condition on a list's rows takes every test a row must pass
  at once, since two conditions in any row can be met by two different rows: the EQ peril
  is included only when one row holds both the peril and its cover ticked. Counting and
  totalling the rows that pass says the rest, at least two perils included or the
  jobsites' costs above a figure, where the number of a table's rows alone says nothing of
  a table that gives a row for each value, as every one of those is typed.
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
- **Fields close up within their component.** A field that applies only while a box is
  ticked, such as the four earthquake fields of a building's rating under its earthquake
  box, would otherwise need a section of its own for each such box, a dozen for one table.
  A Collection already flows its fields through its columns and a Table lays its columns
  side by side, so a field that hides is one fewer in that flow, and closing it up moves
  only the fields after it within the same component, in their order: nothing outside the
  component moves, and nothing overlaps. The component keeps its size, so a section's
  layout stays as the builder made it. A field's conditions are written as a component's
  are and decided in the same rounds, so a field hiding only ever hides more, and a value
  only hidden fields take is left out of the quote as one only hidden components take is.
  The conditions that apply are a whole column's: a Table's cell does not hide row by row
  by its own row's values.
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
  after the schema editor has asked, and a group it empties, which would hold nothing to
  test, goes too.
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
- **Read from the form folder, not copied.** A Workbench Input and a Workbench Output once
  kept copies of the tables in their configs, and the Input a copy of the sample, so that
  runs, tests, generated code and deploy never read the form; the editor kept the copies
  current and deploy held them to the form. Every schema change then showed twice in a pull
  request, and a copy out of step was a failure that existed only because there was a copy.
  The reason for the copies went once the form was a folder in the pipeline folder,
  versioned with the pipeline file and read by deploy anyway, so the nodes read the folder
  wherever the pipeline runs from its source (decided 2026-10-28): the schema gives the
  ports and the request's shape, and the sample is made as the sheets make a quote, its
  hidden values and preset rows as they give them, never its typed rows read raw. A
  deployed pipeline and a release run from a bundle, which carries the tables they were
  built with, so nothing deployed reads a folder. The tables and sample a node runs on are
  in its cache key, so no cached result outlives them, while the pipeline's own revision,
  which its saves are checked against, stays its own files: a form saved, edited by hand or
  switched with a branch reaches the editor as the form's own change does, and saving the
  form never makes the pipeline's save that follows it stale.
- **The workbench's sample, not a file.** Quotes keyed in through a workbench leave no sample
  file, and the workbench is where the quote's tables are laid out, so it is where a pricing
  analyst types the quote previews run on. Read from the form folder like the tables, it is
  the quote Preview shows and prices, saved with the form; read as a request is, it gives
  the rows a request would; read whole, every reader finds the same misfit, and resolving
  points, which reads only the tables, never does. A table it gives no rows is one row of
  nulls rather than none, so every node's expressions still run on a row and its preview
  shows one. Deploy takes the request's shape from the tables, as a deployed pipeline never
  sees the sample.
- **Connections follow names.** The form may reorder or replace its tables wholesale, so a
  table's position says nothing about which table it is, and matching by position would
  quietly reconnect a downstream input to another table. A table's name is also its key in
  the request.
- **The tables are the form's, not the history's.** Fetches can settle out of order, so only
  the newest publishes, and graph edits, undo and redo never start one. Nothing of the
  tables is in the graph, so the history never holds them and an undo never puts back
  tables the form no longer has; a connection the history puts back to a table that has
  gone is removed again, as it was when the table went.
- **Preview's drawing, the app's data.** Preview already shows the sheets as an
  underwriter uses them: nothing selected or moved, the tabs read-only, no section bars.
  The app mounts the same components over the same stores, which it fills from its own
  routes, its quote in the sample's place, priced as it is typed as the sample is, so what
  an analyst sees in Preview is what an underwriter sees, and the builder's palette,
  toolbar, schema editor, panels and shortcuts are never mounted.
- **One release.** The form, the sheets and the pipeline ship in one release, so form
  v12 prices on pipeline v12 by construction, and a page that holds another release's
  form is refused rather than priced on a contract it was not drawn for.
- **The form's own tables.** Analysts read the quotes underwriters save for their own
  reporting, so a quote's data is the form's tables in the user's database, typed as the
  schema says and joined on the quote's identity, rather than a schema of Haute's holding
  JSON a report would have to parse. A quote is its current state: a save replaces its rows,
  no revision is kept, and what happened to it is the activity log, so a copy of an offer is
  made by duplicating the quote.
- **One store, two databases.** A quote saved by `haute app serve` on an analyst's machine
  and one saved by a hosted app go through the same code, so what the SQLite file proves
  holds for the server database; only the connection differs. SQLAlchemy Core, which MLflow
  already installs with Haute, speaks to SQLite and the server engines alike.
- **Required gates final, never a save.** An underwriter saves whenever they like, a draft
  with any field empty; only a value the store cannot hold as its column's type refuses a
  save, since writing it would lose it. Required is what a quote needs before it is offered,
  so Mark final checks it, inputs and outputs alike, and what later work gates behind final,
  exports first, never sees a quote missing one.
- **What the pipeline sees never moves under a price.** The details the app changes over a
  quote's life (its stage, who it is with, whether archived, who changed it last and on which
  Haute) never reach the pipeline, so assigning or archiving a quote leaves its stored price
  the one its stored inputs give.
- **The response's tables, filled by name.** The workbench defines what a priced quote fills
  in as it defines the quote, so the pipeline's part is to connect to each table a frame that
  holds its columns under the schema's names and types. Matching names exactly leaves the
  schema the one place a column is named. A column missing or of the wrong type, and a table
  not connected yet, are visible in the panel and as nulls in the preview rather than stopping
  every run while the pipeline is being built; a report of them is later work.
- **Tables, not a document.** The workbench's output tables are tables, and a pricing analyst
  checks them as tables, so the node's result is its tables and its preview a dataframe per
  table, as a Workbench Input's is per input table. The response document is how the tables
  travel in a request's answer, so it is built where the pipeline answers one.
- **The tables are the response.** A Workbench Output's response is its tables under their
  names, as a quote holds its tables, built where the pipeline answers a request and rendered
  as a Quote Response's is; nothing is placed at a path, so the response has exactly the
  tables' shape and deploy serves it unchanged.
- **One quote per request.** A workbench prices one quote at a time, so a Workbench Output
  answers one, and a one-row table with other than one row fails rather than joining rows
  by position. Its tables' rows carry the quote's reference and option, which say whose
  they are once rows of many quotes are put together: the app's kept quotes, exports and
  any pricing of a book of quotes again.
- **Ports are the tables, inputs are the nodes.** Every node receives a frame under its
  source's name, and codegen, parsing, projection and tracing rely on it, so the table a frame
  fills is the connection's target port, as an Edge Join's role is, rather than its input
  name. The price is that a node fills one table.
- **Buttons laid out before they run.** Where an Import or an Export sits, what it says
  and which tables and columns it touches are the sheets' design, decided with them and
  kept in the form beside the schema whose ids they name, so a rename keeps them and a
  removal takes them away as it takes a field; how they run (the route, the drivers and
  the credentials) is WB-09's and WB-11's to decide, building on that shape. They are two
  kinds rather than one Button with an action, or one per format: an Import names one
  input table and where its columns come from, an Export the tables it writes, and a
  format is a choice within either, changed without laying the button out again. A
  connection is a name the environment resolves, never a credential, as a Data Input's
  is. Until they run, a press does nothing and the button is drawn disabled, rather than
  hidden, so a sheet is laid out as it will be used.

## Interactions

- [server-api](../server-api/high-level.md): `haute.server` includes the workbench router
  with its feature routers and owns the response and request models; the application's
  exception handlers answer a workbench problem as 409, and a stale save answers the
  pipeline save's `stale_document_revision` code.
- [cli](../cli/high-level.md): `haute init --workbench` writes the table and the blank form
  folder, in a new project or in one that has a `haute.toml` without them.
- [pipeline-config](../pipeline-config/high-level.md): the `[workbench]` table in the shared
  `haute.toml` schema, the starter form folder among the scaffold's templates, and the
  `workbench_input` and `workbench_output` decorators and their config folders, whose
  files hold neither tables nor a sample.
- [deploy](../deploy/high-level.md): `DeployConfig.from_toml`'s whole-file check accepts the
  `[workbench]` table; save and deploy refuse a second request input or response node, deploy
  reads the form's tables into the bundle, takes a Workbench Input's request schema and
  dry-run quote from them and reads a served request into them, and a deployed pipeline
  answers with a Workbench Output's response as with a Quote Response's; deploy refuses a
  form it cannot read, one that does not fit the pipeline and one that names a column its
  schema does not hold.
- [git-integration](../git-integration/high-level.md): an unborn repository's seed commit
  takes the pipeline folder's `workbench/` with the pipeline's files; a form save is one
  save on the clone's ledger through the pipeline save's capture, its commit message naming
  the tables and sheets it changed, reported in the editor through the shared save-capture
  report, and a milestone's sweep takes the form's tracked files and the pipeline folder's
  new ones.
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
  nothing then; the request inputs share one singleton slot and the response nodes
  another, a workbench node's ports are the tables the workbench store fetched, its
  connections to a table they no longer have removed outside the undo history, and a
  Workbench Output's connections land on its tables' ports.
- [caching](../caching/high-level.md): the tables and sample a Workbench Input runs on, and
  the tables a Workbench Output fills, are given to the node from the form before it runs
  and classified as node config, so they are in its cache key, and a Workbench Input's
  tables have no input snapshots.
- [assistant](../assistant/high-level.md): the Workbench Input's and Workbench Output's node
  cards and the operations that refuse to author either.
- [frontend-preview-explore](../frontend-preview-explore/high-level.md): a Chart draws
  through the lazy chart runtime and the theme Explore's charts use.

## Failure model

- A `[workbench]` table that is not a table, lacks `enabled`, gives `enabled` something other
  than `true` or `false`, or holds any other key, and a `haute.toml` that cannot be read, is
  not UTF-8 text or cannot be parsed when a workbench route reads it, each fail that route
  with 409 and a message naming the key or the file and what to set; `form` is refused
  with "[workbench].form was removed: the form is the pipeline folder's workbench/ folder;
  delete the key". The form folder is held to the project as every path is: a `workbench`
  folder, or a file in it, that is a symlink or junction to somewhere outside the project is
  refused. The editor shows the message as one error toast and goes on; the
  table is read again on the next request. `haute deploy` refuses an unknown key under
  `[workbench]` as it refuses any unknown `haute.toml` key.
- While the workbench is enabled, a form folder that cannot be read fails
  `GET /api/workbench/tables` and `GET /api/workbench/form` with 409 naming the file and the
  first thing wrong with it. The folder does not exist: "<folder> does not exist: restore it
  from version history (a project starts its workbench with haute init --workbench)"; it has
  no `form.json`; a table or sheet the index names has no file, a sheet's file has no
  layout file or a layout file no sheet; a file is in the folder that the index does not
  name; the index names a table or sheet twice, or two whose names differ only in case, or
  a file's name on disk is not its table's or sheet's name in lower case; a file cannot be
  read, is not UTF-8 text, is not JSON, holds a key twice or does not fit its shape, the
  message naming the place; an identity column is written, a table's file names two
  columns alike, `schema/quote_details.json`
  sets something Haute's, an output column holds a rule, a default or `row_per_value`; a
  sample file is for a table the schema does not hold or for an output table ("rename it
  after its table or remove it"), or holds no rows; a layout file places a section or
  component its sheet does not hold, or leaves one out; `version` is not 1; the folder
  changed each of three times it was read ("<folder> changed while it was being read: try
  again"). A section that a component reaches below (its `y` plus its `h` above the
  section's `height`), or a sheet one reaches past (its `x` plus its `w` above the
  sheet's `width`), as a merge or an edit by hand can leave them, is grown to hold it
  as the folder is read, nothing moved, and the editor says what grew; a save of a form
  laid out so is refused, naming the section or the sheet and the component. A fetch of the
  tables that fails shows one error toast and leaves the ports as the last fetch gave them,
  removing no connection; only the newest fetch counts. A read of the form that fails is
  shown in the view in place of the sheets, with a way to try again.
- `GET /api/workbench/params` answers whether or not the workbench is enabled, since
  Banding's Import from params reads it too, and 409
  with why a file or a column cannot give values: the project has no pipeline folder,
  the name is not a CSV directly in `files/params/`, the file does not exist, is empty,
  is neither UTF-8 nor Windows-1252 or cannot be read as a CSV, or the column is not in its header, holds no
  values or holds more than 5,000. `haute app build` refuses a form with an input column
  whose file can no longer give its values, naming the form and the column, before the
  release directory is touched.
- `haute app build` and `haute deploy` refuse a form whose sheets, conditions, Imports or
  Exports name a table or column its schema does not hold, naming each file, place and
  name, before anything is built or deployed; the editor opens such a form and shows each
  where it is, as it shows a field on a column dropped. A sample value under a name its
  table does not hold stops neither: the sample prices it nowhere, and a release ships no
  sample.
- `PUT /api/workbench/form` answers 404 while the workbench is not enabled; 409
  "stale_document_revision: <form> changed on disk after the workbench read it. Reload the
  workbench before saving." when the folder's revision is not the `base_revision` quoted
  (the folder gone included), writing nothing; 409 "<form> could not be written: <reason>"
  when a file cannot be written or deleted, every file put back as it was, the message
  naming any file that could not be put back, to restore from version history; 422 naming each
  thing the folder cannot hold (The form folder), writing nothing; and FastAPI's 422 for
  a body that is not a form, as any typed body answers. A sample cell that is not text or a
  tick (a number, a null) is not a form.
  Saves take turns under the pipeline save's lock: of two quoting one revision, the second
  finds the first's and is refused. The
  view reports a stale refusal in a toast and a banner, keeping the edits until Reload, and
  any other failed save in one error toast naming the cause. A save that lands but whose
  capture failed or was skipped answers 200 with the warning, as the pipeline's save does;
  the view toasts it, and asks for a git identity once per session when the capture waits
  on one. A folder that could not be read again after the pipeline's document was adopted
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
  without a Workbench Output, a sample that does not fit
  its tables or the preview route's failure, shows as the toolbar's reason and empties the
  output columns, never as a toast; an answer that arrives after the view has left is
  dropped.
- A quote that breaks a column's rule is priced all the same: its cells are marked once
  anything is typed, and nothing is refused. A pricing of the app's quote that fails shows
  "Pricing failed: …" in its bar and empties the output columns, as the sample's does, and
  a save then keeps the quote unpriced.
- An app served by `haute app serve` refuses a request for a host that is not loopback, or
  one carrying `Forwarded` or an `X-Forwarded-*` header, with 400 before it signs the
  request in, so such a request reads, prices and changes nothing as the analyst, the
  health check included. A hosted app is reached through its host's front, which sets
  those headers, and is not held to it.
- The schema editor refuses nothing: a problem is shown beside its table, and a save
  writes the form as it is. The tables route then answers the structured 422 for a table
  the pipeline cannot take, which the tables fetch shows as one toast, so the problem is
  visible in both views until it is fixed.
- A condition that cannot hold as written is refused nowhere: the form reads, saves and
  serves its tables with it, the condition never holds, so what it is on stays hidden and
  the values only hidden components or fields show are left out of the quote, and the view
  names the problem on the sheet or component while building. A condition on the price on
  what takes values typed is named so too, and never applied. A `show_when`, a condition or a
  test the form's shape does not have (an unknown `op`, a `values` entry that is not text,
  a group with no conditions or with a group among them, a condition in any or every row
  with no test, a total that is not a sum, least, greatest or average, a count that is not a
  whole number of 0 or more, days that are not a whole number from -36,500 to 36,500)
  is a form that does not fit, refused as any other.
- `GET /api/workbench/tables` answers 404 while the workbench is not enabled; the editor
  never asks then. Tables or response tables the pipeline cannot take, such as a table named
  with a Python keyword, two tables of one name or two that differ only in case, answer the
  structured 422 of a public contract error (`workbench_tables_invalid`), the message naming
  the table.
- A failed status request shows one error toast; the editor behaves as it does with the
  workbench not enabled.
- A Workbench Input and a Workbench Output take their tables from the project's form, so
  previewing or running either in a project whose workbench is not enabled fails with "The
  Workbench Input reads its tables from the project's workbench, which haute.toml does not
  enable." (the Workbench Output's alike), one whose form folder cannot be read fails with
  the folder's own reason (above), and deploying either and building a release are refused
  the same way.
- A Workbench Input with no tables has no ports, and nor has one whose tables have no
  columns yet. Previewing either fails with "This Workbench Input has no tables: add input
  tables to the workbench's schema and save it." or "This Workbench Input's tables have no
  columns yet: add their columns in the workbench's schema and save it.", and deploying it
  fails for the same reason. A config holding `tables` or `sample`, as one an earlier Haute
  wrote holds its copies of them, is refused wherever it is read, the pipeline file running
  on its own included, with "workbenchInput config contains the removed tables and sample
  fields. A Workbench Input reads its tables and sample from the form folder: delete them.",
  naming the keys it holds; the editor opens such a pipeline in recovery, where the node's
  repair removes them. A connection out of a Workbench Input from a table it does not
  have, as one written into the pipeline file by hand can be, fails the run naming the
  table.
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
  `Pipeline.score` as two Quote Inputs do. `Pipeline.score` refuses a Workbench Input
  connected on a table its form does not have with "Workbench Input '<node>' is connected
  on '<table>', which the workbench's form does not have.". A config key a Workbench Input does not declare,
  `path` among them, is refused as it is for any node type.
- Running a Workbench Output, in a preview, `run()`, `score()` or a deployed pipeline, fails
  with a message naming its table:
  - with no tables, "This Workbench Output has no tables: add output tables to the
    workbench's schema and save it.", and with tables that have no columns yet, "This
    Workbench Output's tables have no columns yet: add their columns in the workbench's
    schema and save it.";
  - for a connection to a port that is no table of the node's;
  - for a one-row table whose frame has other than one row, saying how many it has.

  A table nothing is connected to, a column its frame lacks and a column whose type does
  not fit fill with nulls and fail nothing. A config key a Workbench Output does not
  declare is refused as it is for any node type; `mapping`, which it no longer has, is
  refused wherever its config is read, the pipeline file running on its own included,
  saying each column is filled from the frame's column of exactly its name, and `tables`
  is refused as a Workbench Input's is, saying the tables are read from the form folder.
- Two response nodes of either type, at the top level or in a submodel, fail save and deploy
  with "Only one Quote Response or Workbench Output node is allowed per pipeline (found 2).",
  and `Pipeline.run` and `Pipeline.score` refuse the pipeline, naming both.

## Approved change contract — the quote store as the form's own tables

This section specifies the quote store's environments, schema changes and server databases
before they are built. Every record in it is unresolved until its roadmap package lands. Each
package folds its part into the present-tense sections of this specification and of
[cli](../cli/high-level.md), WB-43 also into the container build's row in
[deploy](../deploy/low-level.md)'s module map, and the last package to land removes this
section. The [low-level contract](low-level.md#approved-change-contract--the-quote-store-as-the-forms-own-tables)
names the seams, shapes and tests.

- **Current limitation.** The store is the form's own tables, in dev and in the hosted
  environments a project declares, changed by one planner (the quote store, in Behaviour), on
  SQLite alone; a started app checks its database's catalogue but not what the store may do
  there.
- **Unresolved target.** The store runs on SQL Server and PostgreSQL besides SQLite, each
  signing the app in as its environment declares, and a started app checks the privileges the
  store needs ([WB-43](../roadmap/workbench.md#wb-43--the-store-on-sql-server-and-postgresql));
  a browser journey saves a quote in `haute app serve`, reads it with SQL, marks it final,
  amends it and saves it again
  ([WB-42](../roadmap/workbench.md#wb-42--the-stores-schema-changes-and-environments)).
- **Non-goals.** In test and prod a type, rows or role change on a column or table that holds
  values is refused, never converted; Haute drops no column or table there, adopts no table it
  did not record, and touches none of the database's other tables. No laptop reaches a hosted
  environment. MySQL, MariaDB, Oracle, Databricks SQL and Snowflake are not store engines.
  The SQL Export, outcomes and policies are unchanged
  ([WB-11](../roadmap/workbench.md#wb-11--exports-from-the-app),
  [WB-13](../roadmap/workbench.md#wb-13--managing-quotes-in-the-underwriter-app)).
- **Failure and compatibility semantics.** A started app lacking a privilege the store
  needs refuses to start, naming it. Nothing falls back: a value the store cannot hold as its
  column's type is refused, never kept as something else.
- **Acceptance evidence.** Each package's acceptance tests, in the testing scenarios of the
  low-level contract.
- **Roadmap package.**
  [WB-42](../roadmap/workbench.md#wb-42--the-stores-schema-changes-and-environments) and
  [WB-43](../roadmap/workbench.md#wb-43--the-store-on-sql-server-and-postgresql), in that
  order.

**Environments on a server.** An environment on a server database names how the app signs
in, `managed-identity`, `service-principal` or `password`, with the names of the variables
holding any secret, never a value:

```toml
[workbench.store.test]
provider = "sqlserver"
auth = "managed-identity"

[workbench.store.prod]
provider = "sqlserver"
auth = "managed-identity"
```

The host's own environment says where it is: `HAUTE_STORE_SERVER`, `HAUTE_STORE_DATABASE`
and, optionally, `HAUTE_STORE_SCHEMA`, or `HAUTE_STORE_URL` holding a whole connection URL. A
project lays its environments out as it likes, a database each, a schema each or both: Haute
qualifies every name with the schema, the connection's default when none is named.

**Starting the app.** A hosted app, before it serves, checks besides its catalogue that it
holds the privileges the store needs, and refuses to start naming each it lacks.

**Database providers.** A provider is a family of engines the store runs on, one interface
each: how it connects and signs in, its types, the binary collation that keeps the identity
and every other exactly-compared text exact, the bounded lengths of Haute's indexed columns,
its rename DDL, its identifier limits and whether its DDL is transactional. A provider
enforces primary, unique and foreign keys, runs a transaction across tables, and runs DDL
inside one so a failed migrate rolls back. `sqlserver` covers SQL Server 2019 and later, Azure
SQL Database and Azure SQL Managed Instance, through pyodbc and Microsoft ODBC Driver 18, which
the app's image installs when an environment names it; `postgresql` covers PostgreSQL and its
hosted forms (Azure Database for PostgreSQL, Amazon RDS and Aurora, Cloud SQL, Databricks
Lakebase), through psycopg; `sqlite` is dev's. Sign-in by managed identity or service
principal goes through Microsoft Entra ID where the engine is Azure's. MySQL, MariaDB and
Oracle, whose DDL commits as it runs, and Databricks SQL and Snowflake, whose keys are not
enforced, are not store engines, though any of them can read the store's tables.
