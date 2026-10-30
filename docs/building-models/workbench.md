# Workbench

The workbench is where a pricing team lays out the quote an underwriter keys in: the
**schema** of typed tables the pipeline reads, and the **sheets**, each a stack of
**sections** holding Tables and Collections that show those tables' columns, Charts that
plot them and Headings that name them. It is part of Haute, switched
on per project, and what it holds lives in the pipeline folder, in `rating/workbench/`,
versioned beside the pipeline: a file for each table and each sheet
([The workbench's files](#the-workbenchs-files)).

!!! note "Enabling it"
    `haute init --workbench` switches it on: in a new project, or in one that has no
    workbench yet, it adds the table to `haute.toml` and writes the blank workbench's
    files.

    ```toml
    [workbench]
    enabled = true
    ```

    While it is enabled, the node palette offers the [Workbench Input](nodes/workbench-input.md)
    in place of the Quote Input and the [Workbench Output](nodes/workbench-output.md) in
    place of the Quote Response, and **Workbench** appears beside **Pricing** at the bottom
    of the palette.

## Switching views

**Workbench** at the bottom of the node palette shows the workbench over the pipeline
editor; **Pricing** brings the pipeline back. The pipeline editor keeps everything it had
(its document, its undo history, live sync) while the workbench shows; its keyboard
shortcuts are off meanwhile, so nothing you press in the workbench edits the pipeline. The
toolbar keeps Haute's project controls (**Pipeline files** and **Local files**, Assistant,
Help, the branch, **Save** and **Commit**) and the source dropdown over the **Pipeline**
button, and shows the workbench's own: **Undo** over **Redo** and **Zoom In** over **Zoom
Out**, in the same places as the pipeline editor's, then **Build** over **Preview** and
**Schema** over **Display**. They stay in place in both modes: Display is greyed out in
Preview. Preview prices the sample on the source the dropdown shows.

**Save** and Ctrl+S save the project, the same from either view: the workbench's files
that changed first, when the workbench has unsaved edits, then the pipeline, with the
Workbench Input's and Workbench Output's tables fetched again in between, and each
save is recorded on your branch's save ledger, so it shows in the Git panel's history.
**Commit** records a milestone of the whole project after that save. A save is refused
when one of the workbench's files changed on disk since the workbench read them (an edit
by hand, or a branch switched outside Haute), and then the pipeline is not saved either;
the banner's **Reload** reads them again, dropping your unsaved edits. When one of them
changes on disk (an edit by hand, a pull, or a branch switched), the workbench follows
it, unless you have unsaved edits, when the banner appears instead. A layout file that
leaves a component below its section or past its sheet's edge, as a merge or an edit by
hand can, opens with that section or sheet grown to hold it, nothing moved: a notice
says what grew, and the next save keeps it.

Both workbench nodes' panels have **Edit in Workbench**, which opens the view.

## The workbench's files

The workbench keeps what it holds in the pipeline folder's `workbench/` folder, a JSON
file for each part, so a pull request shows each change in the file of the table or
sheet it changed. A save writes only the files that changed.

| File | What it holds |
|---|---|
| `workbench/form.json` | The project's name, the sheets' colours set under **Display** in the toolbar, and the order of the tables and of the sheets |
| `workbench/schema/<table>.json` | A table: its role, its rows and its columns with their settings. `quote_details.json` holds only what the project sets on Haute's own table |
| `workbench/sheets/<sheet>.json` | A sheet: its sections, and their components with what they show and how they look |
| `workbench/sheets/<sheet>.layout.json` | Where the sheet's components sit: the sheet's width, each section's height, and each component's place and size |
| `workbench/sample/<table>.json` | The sample's rows for an input table, while it has any |

A table's files are named after the table in lower case, and a sheet's after its title
(**BR Coverages** is `br_coverages.json`). Each file says only what is set: a setting left
at its default is not written. A component names a column as `table.column`, or by the
column's name alone where its table is already given. Each table and column keeps the id
written beside its name for good, so renaming one keeps the values saved quotes hold for
it. The quote's reference and option lead every table without being written in its file.

The workbench opens only a folder whose files `form.json` lists: a file it doesn't list,
a listed file that is missing, a file named in capitals, or a table's file naming two columns
alike stops it opening, with a message naming the file. A missing folder is restored from
version history; the workbench never starts it again blank. A column that a sheet names but
the schema doesn't hold, as an edit by hand can leave, shows as a missing column on the
sheet, and a deploy refuses the project until it is put right.

## Schema

**Schema** in the toolbar opens the schema in a pane over the left of the sheet; **Schema**
again, or the pane's close button, closes it, and it opens again as you left it; dragging
its right edge widens it. **Search fields** at its top shows only the columns whose name or
label holds what you type; while it does, columns and tables can be edited or removed but
not added or moved. Clear it, or press Escape in it, to see the whole schema again. The
sheet stays where it is under the pane, and the pane stays open as you switch sheets or go
to Preview, where it shows the schema without letting you change it.

The schema holds the tables the sheets work with, and the pipeline's tables. Each table has a name, a role —
**Input**, sent to the pricing engine, or **Output**, returned by it — and its rows: **One
row** per quote, such as policy details, or **Many rows**, such as an equipment schedule.
Each column has a type (Text, Integer, Decimal, True/false or Date, drawn as the step editor
draws a value of that kind and changed from its marker), a name and, in a many-row table,
whether it is a **key**, part of what says which row is which. **Label, help, rules and
format** beside a column opens its details: its label; its **group**, a name you give the
columns that belong together (type the same group on each; the table's groups are offered
as you type), which a Table can show over its labels; its **help**, shown as a
small mark beside the label on the sheets; for an Integer or a Decimal, its **format**
(General, Number, Percentage or a currency), decimal places and thousands separators, as
the Explore pivot offers them; and, in an input table, its **default**, its
**placeholder**, what an empty cell shows (any type but True/false and Date), and the
rules an underwriter's value must meet: required, a range for a number, the allowed
values. A number shows in its format wherever the sheets show it, and in its default, a
percentage as the value times 100, so you type 0.125 for 12.5%; while you type into a cell
or the default it shows as typed, and what is typed is what is saved and priced. Under
each column's name, a line sums up what is set: required, its range and default, shown in
its format, its allowed values, and a number's format and decimal places.

Every table but `quote_details` starts with two columns Haute keeps for the quote,
**quote_reference** and **quote_option**, which say which quote a row belongs to once rows of many
quotes are put together, in the app's saved quotes, in exports and in joins. They come
first and are locked: they cannot be renamed, retyped, moved or removed, take no label,
group, help, rules, default or format, and are not offered to the sheets, where
`quote_details`' own stand for them. They are not a key: rows still line up by the table's
own key. A table you add has them at once, and a project saved before them takes them on
when it is opened.

A column's **allowed values** come from a CSV in the pipeline's params folder
(`rating/files/params/`, **params** in the Pipeline files panel); they are never typed in.
Choose the file beside **Allowed**, then its column: the column's distinct values become
the allowed values, each as written (a code like `01234` keeps its zero), trimmed, blanks
left out, in the order the file first gives them. A CSV saved from Excel reads as it is.
The values are copied in when you choose them; after the file changes, the refresh button
beside them reads them again, and `haute app build` always reads them from the file as it
is, so the underwriter app allows what the file holds. A file or column that cannot give
values (gone, unreadable, with no values or more than 5,000) says why under them, and stops
`haute app build`. Choosing the blank file takes them away. Values typed in by hand, which
an older form may hold, show beside **Allowed** as typed in and are named among the
schema's problems until they are read from a params file or taken away.

In a many-row input table, **Rows** under a column's allowed values gives the table **a row
for each** of them, in their order. A `coverage` table whose `peril` column takes its values
from `perils.csv` (AOP, EQ, NW, WD, SCS, FLD) then has exactly six rows, one for each peril,
and the underwriter types the other columns on each. The Tables that show it have those rows
and no rows can be inserted or deleted; the peril cells show the peril and take no typing. A Table showing
it beside another table whose rows differ shows every row of both and says their rows do
not line up. Every row goes
to the pipeline once the quote is priced, a peril with nothing else typed included, but the
perils alone are not typing: a quote holding nothing else is not priced, marked or saved.
The rows follow the params file: refresh the values, or build the app, and a new peril gets
a row, while what was typed for a peril stays with it. Only one column in a table gives its
rows, so ticking another unticks the first. Make the column the table's key if an output
table keyed by peril should line up with the rows.

A column's **default** is the value a new quote starts with in it, as if the underwriter
had typed it: ticked or not for a True/false, one of its allowed values when it has some,
a date for a Date, or typed. Only a one-row input table's columns take one. Build shows a
new quote with its defaults, and the underwriter app's **New quote** window starts with them; the
sample you type in Preview, and a quote made from another quote, keep their own values. A
quote holding nothing but its defaults is not priced, marked or saved until something is
typed, and a value typed equal to its default counts as the default.

- Enter in the last column's name adds the next column; in any other column's name it just
  keeps what you typed. A column added, by Enter or **Add column**, takes the type of the
  column above it: Text when it is the table's first or comes under the index.
- Drag a column by the grip at its left, or press Alt+Up and Alt+Down in its name, to move
  it within its table. Drag a table by the grip beside its name, or press Alt+Up and
  Alt+Down in its name, to move the table. A grip shows while the pointer is over its row.
- **Add index** on a many-row table adds a column first in the table after
  **quote_reference** and **quote_option**, `row_number` until renamed, that numbers the
  rows from 1. It is an Integer and can be the table's key.
- The editor says what would stop the schema being the pipeline's tables: a table's name is
  held to the rules a Quote Input's labels follow (an identifier, no reserved name, unique
  whatever its case), a column's name is an identifier that is not a Python keyword, unique
  in its table, a many-row table needs a key, an input column's range must not be inverted
  and its allowed values must be of its type and read from a params file, a True/false or
  Date column can have none, a default must be a value the column takes (of its type,
  in its range, one of its allowed values), and only one column in a table gives it a row
  for each value. Changing a column's type drops the rules and
  the format the new type cannot have, a row for each value going with the allowed values,
  and the default when it becomes or stops being
  True/false, or becomes a Date while it is not a date; switching a table to many rows
  clears its defaults, and to one row its keys and its row for each value.
- Removing a column or a table a sheet shows asks first, and takes it off the sheet.
- A column of a table's own named **quote_reference** or **quote_option**, as a project saved before
  Haute kept those names may hold, keeps its place and its values, and the table goes
  without the quote's reference and option; the editor names it among the schema's
  problems, and `haute app build` and deploying refuse while it stands. Rename it, and the
  table takes them at the next save.

### Quote details

Every workbench has Haute's own table, `quote_details`, first in the schema, where
it stays: one row of a quote's administration, the same for every company and line of
business, which the underwriter app keeps with each saved quote and lists on its Quotes
page. Its columns are fixed, in this order: **quote_reference**, **quote_option**,
**quote_name**, **insured**, **broker**, **inception_date**, **expiry_date**,
**is_renewal**, **stage**, **assignee**, **archived**, **created_date**, **created_by**,
**modified_date**, **modified_by** and **haute_version**. Put them on the sheets as any
other columns, and give them labels, groups, help and placeholders, and the ones your
underwriters type defaults and rules; the pipeline receives the table as it receives any
other input, so a rule can read a quote's inception date or whether it is a renewal.
Anything else about a quote, its currency or how long the offer stands, goes in a table of
your own.
**is_renewal** is a tick box: a quote made with the app's **Renew** opens with it ticked,
and an underwriter ticks it for a renewal started any other way. A project saved by an
earlier Haute takes today's columns when it is opened: its **reference** is named
**quote_reference**, **valid_until** and **currency** are taken out, and the newer columns
are added, the labels and rules you gave the others kept. A field on a column taken out
shows **Missing column** on the sheets while you build, for you to remove or move to a
column of your own, and is not shown in Preview or the app.

The app sets **quote_reference**, **quote_option**, **stage**, **assignee**, **archived**,
**created_date**, **created_by**, **modified_date**, **modified_by** and **haute_version**
itself: their cells take no typing on the sheets in the app and take no rules and no
default. The underwriter keys the **quote_reference** once, in the window that makes the
quote; it never changes after. **created_date** and **created_by** say when the quote was
made and by whom; **modified_date** and **modified_by** when it was last saved or changed
and by whom; **haute_version** is the version of Haute the app ran then. The two dates are
dates in UTC, so near midnight they can differ by a day from the time the app shows you.
The pipeline receives **quote_reference**, **quote_option**, **created_date** and
**created_by**, and every other column of `quote_details`, whatever the sheets hide.
**stage**, **assignee**, **archived**, **modified_date**, **modified_by** and
**haute_version** are kept with the quote and never reach the pipeline, so assigning or
archiving a quote never changes what it was priced on. In Preview the columns the pipeline
receives hold what you type into the sample, to try a rule that reads them; an app without
a quote store leaves them blank. Assigning, archiving, restoring, marking final and
amending a quote change those details alone, its values and price kept, and its activity
shows when a quote moved. A quote takes
its reference and option when it is made (see [The Quotes page](#the-quotes-page)), so its
every price has them. Preview prices the sample as `PREVIEW`, option 1, as the app prices
the one quote it holds without a quote store: a quote's reference and option are never
blank.

A table or a column you add reaches the pipeline when you save: the Workbench Input and
the Workbench Output read their tables from the workbench's files wherever the pipeline
runs, and the editor fetches them again, so their ports follow the schema. Until then it
is on the sheets alone.

### Joining the schema's tables

Every table but `quote_details` holds the quote's reference and option on every row. Join
two of them on **quote_reference**, **quote_option** and their own key, and each stays once: a Left,
Inner or Right join keeps one **quote_reference** and one **quote_option**, and a Semi or Anti join the
first table's alone. A Full join keeps both tables' keys, the second's as `quote_reference_right`
and `quote_option_right`: in an [Edge Join](nodes/edge-join.md), set **COALESCE** to **True** to
keep one of each; after a Transform's **Join another input**, fill **quote_reference**,
**quote_option** and your own key from their `_right` copies where a row came from the second
table alone, then remove the copies. A cross join takes no keys: remove the second table's
**quote_reference** and **quote_option** after it. Joined on their own key alone, the second table's
are kept as `quote_reference_right` and `quote_option_right`, and joining a third table after that is
refused, as `quote_reference_right` is there already. **quote_option** is an Integer, so a step that
takes every Integer column takes it too.

## Sheets

A sheet is a canvas on a snap grid with a set **width** (1280 pixels to start, and a
sheet you add as wide as the last), set in the sheet's own panel, and as long as its
sections. It stays beside the palette and under the sheet tabs: zoom out and it is drawn
smaller from that corner; wider than the window, it scrolls across.
It is a stack of **sections**: strips across its whole width, one under the next, each
holding the components placed in it. A new sheet is one section; hide a section and, in
Preview, the sections under it move up to close the gap (see
[Showing sheets, sections, components and fields conditionally](#showing-sheets-sections-components-and-fields-conditionally)).
The palette on the left offers six components and a section; drag a component onto the
sheet (a ghost shows where it lands) and it goes into the section under the top of the
ghost. From the keyboard, Tab to an item and press Enter: a component goes into the
selected section (or the selected component's, else the sheet's first) at the first free
place, and is selected so the arrow keys move it; a section goes in after the selected
one:

- A **Table** shows columns of many-row tables as a grid that starts with its number of rows.
- A **Collection** shows columns of one-row tables as labelled boxes, its number of boxes
  across.
- A **Chart** plots number columns, typed or priced. Of one-row tables it gives a point per
  field; of many-row tables, a series per field over the rows, each row labelled by a
  column you choose or numbered. A pie shows parts of a whole, so given a value below 0, or
  only 0s, it says so instead of drawing slices.
- A **Heading** names a section of the sheet, or the sheet itself, in text on the sheet:
  **Large** for a title, **Medium** for a section's heading, **Small** for a label, with an
  optional description under it saying what the section is for, aligned left, centre or
  right. It holds no values; give it the conditions of the section it names and it hides
  with it.
- An **Import** is a button that fills an input table from a CSV or an Excel workbook the
  underwriter picks, or from a database.
- An **Export** is a button that takes the quote's tables out to a CSV, an Excel workbook
  or a database.

For now you can lay Imports and Exports out and set what they will do, but nothing runs
them yet: in Preview and the underwriter app they are greyed out and pressing one does
nothing.

While building, **Display** in the toolbar opens the sheets' colours: the **Sheet
background**, the **Component background** of framed Tables, Collections and Charts, their
**Borders**, the **Font** of Headings and of components' titles, labels and values, and the
fill of **Input cells** and **Output cells**. Each is Automatic, one of the swatches (white,
light grey, dark grey and black, then eight hues) or a colour of your own from the rainbow
square, and the sheet shows a colour as you pick it. They apply wherever the
sheets show, Preview and the underwriter app included.

Select a component to open its properties on the right: its title, when it shows (see
[Showing sheets, sections, components and fields conditionally](#showing-sheets-sections-components-and-fields-conditionally)), its layout (the rows a
Table starts with, whether they are **fixed**, with no rows inserted or deleted, and whether it is
**transposed**, or a Collection's columns across), a Table's **look** (see
[A Table's look](#a-tables-look)), a Collection's **look** (see
[A Collection's look](#a-collections-look)), the **order** of the fields it shows (drag, or
Alt+Up and Alt+Down), and its **fields**, ticked from the schema tables of its kind;
**Search fields** over them shows only the columns whose name or label holds what you type. A
Chart's settings are its **Type** (Column, Line, Area or Pie), **Rows** (what labels the
rows of many-row tables, or Row number), its **Legend**, and whether it is **Stacked**,
**Horizontal** and shows **Data labels**; its fields come from any table, number columns
other than an index. A grid holds one kind of row, so once a Table or a Chart shows a
field, tables whose rows do not line up with it (keyed differently) are greyed out; an
input table and an output table with the same key line up, so each row can show its
premium. Output columns are shaded. A transposed Table turns its grid on its side: each
field is a line, labelled at its left, and each row is a column, numbered along the top;
its stripes and gridlines follow the lines as drawn. With **groups** ticked, the columns side by side
in one schema group share a heading over their labels, as merged cells over a table's
headers would in Excel; a column in no group has none. Transposed, the groups sit in a
column before the labels. Order the fields so each group's columns are together. To
group a Collection's fields, press **Add spacer** under its order and drag the spacer
between two fields (or move it with Alt+Up and Alt+Down): the Collection leaves a gap
there, and the fields after it start a new row. Two spacers together make a wider gap,
and a spacer's cross removes it. A
Table's and a Collection's properties are in two
panes: **Config**, the fields it shows and their order, and **Display**, its title, when it
shows, its layout and its look. A Heading's properties are its **Text** and
**Description**, when it shows, its **Size** and its **Align**ment.

An Import's and an Export's properties are in the same two panes: **Display** holds its
**Label**, the text on the button, and when it shows; **Config** starts with its
**Format**, CSV, Excel or SQL. Changing the format starts that format's settings afresh,
though an Export keeps its file name, headers and values between CSV and Excel.

- An Import from a **CSV** has a **Delimiter** (Auto finds it from the file); from
  **Excel**, the **Sheet** to read (blank for the first) and the **Header row** that names
  its columns; from **SQL**, the **Connection** (a name your environment supplies the
  credentials for, never the credentials themselves), the **Query**, and a row under
  **Parameters** for each `:name` in the query, where you choose the one-row input column
  whose value it takes. **Into** is the input table it fills. For a many-row table,
  **Rows** says whether the rows it brings **Replace** the table's rows, **Append** to
  them, or **Update by key**, matching rows on the table's keys. **Columns** lists the
  table's columns an underwriter types; a column is read from the column of the same name
  or label, whatever its case, unless you type the name it has in the file or the query's
  answer.
- An Export tick-lists the **Tables** it writes, inputs and outputs alike. To a **CSV**
  (one table) or **Excel** (a sheet per table) it has a file **Name**, where `{name}`
  stands for that column of quote_details (`{quote_reference}-{quote_option}` to start),
  **Headers** (the columns' names or labels) and **Values** (raw, or formatted as the
  sheets show them), and a CSV a **Delimiter**. To **SQL** it has a **Connection**, and
  beside each table you tick, the name of the database table it is written to (blank for
  the table's own name).

Drag a component to move it and any of its eight handles to resize it; everything snaps
to the grid. A component stays inside its section: it stops at the section's edges, and
moves into another section only once its top is over it. Resizing a component past its
section's bottom pushes the section's border down. It stays inside the sheet's width
too, stopping at its edges; resizing it past the right edge widens the sheet. With a component selected (click it, or Tab to it), the
arrow keys nudge it (Shift for a pixel), Ctrl and the arrow keys resize it from its
bottom-right corner (Ctrl+Shift for a pixel), Ctrl+D duplicates it, Ctrl+C copies it, Delete
or Backspace removes it and Escape deselects it; Ctrl+Z and Ctrl+Y undo and redo. Ctrl+V
pastes what you copied onto the sheet showing, another sheet too: into the section you
have selected (or the selected component's, else the section it came from, else the
sheet's first), where the original sat, moved down and right past one already there, and
selected so you can drag it into place; a column deleted from the schema since you copied
it is left out of the copy. Ctrl+1 fits the sheet to the
window, and the zoom runs from 25% to 200%. A component with a problem — no fields, a
Heading with no text, a column no longer in the schema, a table of the other kind, a
Chart's field that is not a number or is an index, tables whose rows do not line up, a pie
of many rows with more than one field, an Import or an Export with no label or missing
what its format needs — gets a dashed frame that names the problem. Tab,
and a screen reader, go through the sheet section by section, each section's bar and then
its components as they sit, row by row from the top and left to right along each row,
whatever order you added them in.

To add a section, drag **Section** from the palette onto the sheet: a line shows where it
will start, never through a component (it moves to the nearer edge of one) and never in
the top 48px of a section, too little for the section above (it goes above the section, or
further down). Dropped
inside a section, it splits it there, and what sits below the line moves into the new
section, keeping its place; dropped on a section's bar it goes in above
that section, and below the last section it adds an empty one at the end. While building,
each section sits under a slim bar with its title, so the sheet is a little taller than in
Preview. Click a bar to open the section's panel: its **Title** (yours, for finding your
way; put a Heading in the section for what the underwriter reads), when it **shows**, its
**Height** with **Fit**, **Add section below** and **Delete section**. Drag a bar up or
down, or press Alt+Up and Alt+Down on it, to move its section; Delete removes it (asking
first when components are in it; a sheet keeps at least one section). A selected section
has a handle under the middle of its bottom: drag it to set the section's height, and
double-click it to fit the section to what it holds. A section never shrinks by itself.

Sheets have tabs along the top: **+** adds one, the Left and Right arrows (and Home and
End) go from tab to tab, double-click (or F2) renames one, dragging
a tab onto another moves its sheet there (or press Alt+Left and Alt+Right on a tab), the
settings mark on the showing tab opens the sheet's own panel (its title, when it shows,
its width and its height). The height is the sections' heights added up, and changing it
changes the last section's. While that panel is open, the sheet has a handle on the
middle of its right edge and one under the middle of its bottom: drag them to set its
width and height. **Delete sheet** at the foot of that panel deletes the sheet once you
confirm it in the box that opens, which says how many components go with it; the schema
and the sample stay, and Ctrl+Z brings the sheet back. The last sheet cannot be deleted.

### A Table's look

A Table's **Display** pane sets its look much as you would format a range in Excel. Any
colour you leave on **Automatic** follows the sheets' colours from **Display** in the
toolbar; a colour you set on a Table, or on one of its columns, is drawn over them.

- **Look**: compact or comfortable rows, the **Text size** of its values, and row
  numbers, **groups** and striped rows.
- **Header**: labels **As written** or in **Capitals**, their **Size** (**Auto** is the
  values' size, a pixel smaller in capitals), their **Direction**, their **Align** (Auto
  leaves each label as its column's look places it), a **Fill** and **Font** colour for the
  whole header, **Bold**, and **Wrap**. **Rotated up** and **Rotated down**
  turn the labels to run up or down their column, and **Stacked** puts their letters one
  under another; turned labels sit at the foot of the header, so set narrow column widths
  to fit more columns across. Wrapped labels break between words onto more lines rather
  than being cut off; a turned label that wraps starts a new line beside itself. Group
  names stay level and centred.
- **Lines**: which **Gridlines** to draw (between rows, a full grid or none), their
  **Style** (solid, dashed or dotted), **Weight** (thin, medium or thick) and **Colour**.
  The weight and colour also apply to the rule under the header and the line before the
  output columns, which stay solid.
- **Frame**: whether it is framed and, if so, the frame's **Weight** and **Colour** (the
  rule under its title takes the colour too).
- **Columns**: click a column to open its own look: its header's **Fill**, **Font** and
  **Align** (Auto follows the header's Align, else sits the header as its cells sit, though
  a transposed Table's label sits at its start while its cells' Align is Auto too), its cells' **Fill**, **Font** and
  **Align** (Auto puts numbers on the right), **Width** in pixels (blank for Auto, from 24
  to 1000) and **Bold**. **Clear** takes the column's look
  away; a dot marks a column that has one. A column's fill covers the stripes and the
  output shade in its cells. Once every column has a width, the grid is as wide as its
  columns rather than its frame. Under its look, the column's own **Show when** (see
  [Showing sheets, sections, components and fields conditionally](#showing-sheets-sections-components-and-fields-conditionally));
  an eye marks a column that has conditions, and **Clear** leaves them. When you stop
  showing a column, its look and its Show when go with it.

On a transposed Table, a column's look applies along its field's line: its header colours
on the line's label and its cell colours across the line. Widths don't apply to a
transposed Table, so **Width** is hidden there.

### A Collection's look

A Collection's **Display** pane sets its look the way a Table's does. Any colour you leave
on **Automatic** follows the sheets' colours from **Display** in the toolbar; a colour you
set on a Collection, or on one of its fields, is drawn over them.

- **Look**: compact or comfortable boxes, and the **Text size** of the values in them.
- **Labels**: their **Position**, **Above** or **Beside** the boxes; while beside, their
  **Width** as a percentage of the field (blank for Auto, from 1 to 99), always leaving the
  box at least 24 pixels; **As written** or
  in **Capitals**, their **Size** (**Auto** is the values' size, a pixel smaller in
  capitals), **Align** (Auto puts them at the left), a **Fill** and **Font** colour,
  **Bold**, and **Wrap**. A label with a fill is drawn as a shaded cell,
  as tall as its box when it sits beside it. Wrapped labels break between words onto more
  lines rather than being cut off.
- **Boxes**: their borders' **Style** (solid, dashed or dotted), **Weight** (thin, medium or
  thick) and **Colour**, **Align**, where the values sit in the boxes (Auto puts numbers on
  the right), and their **Width** in pixels (blank for Auto, from 24 to 1000). Labels and
  boxes align apart: labels on the left with values centred, say.
- **Frame**: whether it is framed and, if so, the frame's **Weight** and **Colour** (the
  rule under its title takes the colour too).
- **Fields**: click a field to open its own look: its label's **Fill**, **Font** and
  **Align**, its box's **Fill**, **Font** and **Align** (each Align on Auto follows the
  Collection's), its box's **Width** in pixels (blank for the Collection's) and **Bold**. **Clear** takes the
  field's look away; a dot marks a field that has one. A field's fill covers the output
  shade in its box. Under its look, the field's own **Show when**, as a Table's columns
  have; an eye marks a field that has conditions. When you stop showing a field, its look
  and its Show when go with it.

With labels beside the boxes and both widths on Auto, each label takes two fifths of its
field and its box the rest. Give the labels a width, say 80%, and the boxes take the
rest; give the boxes a width instead and the labels take the rest: for a Collection of
tick boxes in one column, set the boxes' **Width** to about 40 for wide labels and small
boxes. A box never grows wider than its field. With labels above, a box narrower than its
field sits at the field's left, or where the boxes' **Align** puts it.

## Showing sheets, sections, components and fields conditionally

A sheet, a section, a component, or one of a Table's columns or a Collection's fields, can
show only when the quote calls for it: a business interruption sheet only while its cover
is ticked, a flood section only while flood is ticked, a referral note only while the sum
insured is above a limit, the earthquake limit and deductible only while the earthquake box
beside them is ticked. Set it under **Show when**, in a component's properties, in a
sheet's or a section's own panel, or in a column's or a field's own look in its
component's **Display** pane. **Add condition** tests a column of an input table:

- a tick box **is ticked** or **is not ticked**;
- any other column **is filled** or **is empty**, and **equals** or **does not equal** a
  value;
- a text column **contains**, **does not contain**, **starts with** or **ends with** some
  text, its letters cased as typed: an insured containing "Holdings";
- a text or number column **is one of** or **is not one of** some values, ticked from its
  allowed values or typed comma-separated, each number without its thousands separator;
- a number **is greater than**, **is at least**, **is less than** or **is at most** a
  value, and a date **is after**, **is on or after**, **is before** or **is on or before**
  one.

What shows nothing to type can also show by the price: a Heading, a Chart, a Table or
Collection of output columns alone, an output's own column or field, or a section or sheet
holding only those can test an output column, such as a referral reasons Table shown while
**referral_count is greater than 0**. What shows is decided from what is typed first, so
the price never decides what is priced; these then show and hide with the price on screen,
the last one while a new price is on its way, and stay hidden until there is one. A condition
on the price on anything you type into is flagged and does nothing.

A test of one value can compare with another field of the same type instead of a value
you type: pick it beside the test, in place of **Value**. A policy limit **is greater
than** the MGA limit; a completion date **is after** the start date **+ 365** days, a
negative number of days taking them away. In a test of a row, the other field is a column
of the same row. The test holds only while both fields show. There is no test against
today, since a saved quote would then show other sheets on another day: add a date field
for it.

With two or more conditions, choose whether **all** or **any** of them must hold, or **at
least** a number of them: a section shown while two or three of three cover boxes are
ticked needs at least 2 of the three boxes' **is ticked** conditions. There is no "at most"
or "exactly": hiding a box makes its condition fail, so those would show a section again
as more of the form hid.

A many-row table, such as the drivers on a motor quote, is tested by its rows: choose
**Rows of** the table, then which rows, and the tests a row must pass, each with its own
column, test and value. **Add test** adds another, and a row must pass all of them at
once, so a section for a covered earthquake can show while the perils table has a row
whose peril is EQ and whose cover is ticked; two separate conditions could be met by two
different rows. Which rows can be:

- **Any row** or **Every row** passing the tests: a young drivers section can show while
  any driver is under 25. **Every row** needs at least one row, so a list nobody has
  filled in shows nothing waiting on every row of it.
- **Number of rows** passing the tests, every row while there are none: a section shown
  while at least 2 perils have their cover ticked, or a second vehicle section while the
  vehicles table has at least 2 rows.
- **Sum**, **Smallest**, **Largest** or **Average** of a number column over the rows
  passing the tests: a large schedules section shown while the jobsites' hard costs add up
  past a figure. A blank cell, or one that isn't a number, adds nothing; the sum of no rows
  is 0, and the others of no rows fail every test.

Only rows with something typed in them count.

**Add group** adds a group of conditions that counts as one, with its own **Add
condition** and its own **all**, **any** or **at least**. Use it to mix them: a section shown while
the cover is Comprehensive and either a driver is under 25 or the claims count is at least
2 needs **all** of the cover's condition and a group needing **any** of the other two. A
new group starts needing the opposite of the Show when around it, since one needing the
same would change nothing. Removing a group's last condition removes the group.

A section on a hidden sheet is hidden too, as is a component in a hidden section and a field
of a hidden component, and a sheet, a section or a field waiting on a box that sits
somewhere hidden is hidden with it, so a cover and its extensions hide and show in a chain.

A field's conditions can test the other fields of its own component: put the earthquake
box and its limit and deductible in one Collection, and give the limit and the deductible
a Show when of **EQ included is ticked**. A field shown only by its own value could never
show again once hidden, so the view names it as a problem.

While building, a new quote decides, holding its defaults alone: a hidden sheet's tab, a
hidden section, a hidden component and a hidden field are faded, their eye struck through
(hover it to read the conditions), and you can still select and edit them; every section
keeps its place, and every field its place in its component. A sheet's own conditions never
fade what is on it, so a hidden sheet stays easy to work on: its faded tab shows that it is
hidden, and its sections, components and fields fade for their own conditions. In
Preview the sample decides as you type it, and in the underwriter app the quote: a hidden
sheet has no tab; a hidden section is not drawn and the
sections under it move up to close the gap; a hidden component is not drawn, its space
in its section left empty, since components are placed freely; and a hidden field is not
drawn, its component closing up around it: a Collection's other fields move up through its
columns, a spacer going with the fields after it when all of them hide, and a Table leaves
out the column (or, transposed, the field's line). A whole column hides; its cells don't
hide row by row. So give a set of components that should close up a section of its own, with its
Heading in it, and put the tick box that controls it just above it, where the underwriter
is working; fields that hide together with their box can stay in one Collection. While the underwriter holds a mouse
button down on the sheet nothing moves, so a click is never pulled from under them, and
when a section above them opens or closes the sheet scrolls to keep their place.

A quote is what the sheets show. Values typed into hidden cells are left out of what is
priced, as if they had never been typed, for the sample priced in Preview (and what the
Workbench Input's previews run on) and for a quote in the underwriter app, and a hidden required
cell is never marked. The values are kept, so ticking the cover again brings them back. A Chart takes
nothing typed, so a column only a Chart plots never hides and a condition on it never
holds, and a value only a hidden component or a hidden field takes is left out even when a
Chart that shows plots it. A Heading takes nothing either; it just hides and shows. A
condition that cannot work (its column gone or retyped, a missing value) and a sheet, a
section or a field nothing could show again once hidden (its tick box only inside it) are
named on the sheet's tab, the section's header or the component's dashed frame, a field's
by its label.

## Building: a new quote

While building, the sheets look as they will when an underwriter starts a new quote: every
input empty but those a column's default fills, every output a dash, each Table with the
rows it starts with, each Chart with nothing but defaults to chart, and only what a new
quote shows unfaded. The cells take no
typing while building: clicking one selects its component, as clicking anywhere on it does.
Nothing is priced while building.

## Preview

**Preview** shows the sheets as an underwriter uses them, drawn as the underwriter app
draws them: the sections that show stacked one under the next and each component where it
sits in its section, with no section bars and nothing to move or lay out. The layout around
the sheets stays as it was while building, with the palette's components and the toolbar's
building controls greyed out. It is where you
type the **sample**, a quote you key in as an underwriter would: it is saved with the
sheets, and it is what the [Workbench Input](nodes/workbench-input.md)'s previews run on
once saved.

A Collection's boxes hold its tables' one row, and a Table's grid holds its tables' rows.
Unless its rows are fixed, right-click a row for its menu: **Insert** opens **Insert Row
Above**, **Insert Row Below**, **Insert Multiple Rows Above** and **Insert Multiple Rows
Below**, the last two asking how many rows to insert, and **Delete Row** deletes it. On a
transposed Table, right-click a column, or
its number, and the menu inserts columns to the left or right. Rows are inserted and
deleted in every input table the grid shows, so the cells stay side by side. A Table
starts with its rows, empty; the first value you type keeps them, and from then on the
rows are yours to insert and delete, fewer than it started with too, though a Table of fixed
rows never shows fewer. Change a Table's rows while building and Preview shows it: empty
rows after the last one you typed into go, or more are added. A Table showing a table that
has a row for each value (see [Schema](#schema)) shows those rows whatever its own rows
say. Each cell is the control its
column calls for: a tick box, a dropdown of the allowed values, a date picker, or a text
field. An empty cell is blank, or shows its column's placeholder, a number column's in
its format when the placeholder is a number. The sample is part of
what **Save** writes, and **Undo** and **Redo** take back what you type, in Preview as
while building.

The sample is **priced live**: 300ms after the schema or the sample changes, a value
counting once it is entered (press Enter or Tab, or leave a text or number cell; a tick,
a choice or a date counts as you make it), and each time you open Preview, Haute prices it on the pipeline open in the
editor, as it stands, saved or not, exactly as a deployed pipeline would price it, as
quote `PREVIEW`, option 1. Every output column shows its value, a Collection's its table's one row and a Table's the row in
the answer keyed like each grid row, dimmed while a newer answer is on its way, and every
Chart plots the sample with what pricing it gave. A Table of output columns alone, such as
a premium by peril, shows a row for each row of the answer, as a Chart of them does; until
it is priced (or while its table has nothing connected) it shows its starting rows of
dashes. There is no **Price** to press: a sample with nothing typed is not priced, and
its outputs show dashes. **Reset** in the toolbar takes the sample back to a new quote,
its columns' defaults and nothing else; **Undo** brings back what it cleared. The
sample is priced whatever its cells hold. Once anything is typed, a cell that breaks its
column's rules (a required cell left empty, a number that is not one, a whole number that
is not whole, a date that is not one, a number outside its range, a value that is not one of the allowed ones) is
outlined, naming the problem when you hover, and priced all the same. When pricing fails —
no Workbench Output on the pipeline, a value that does not fit its column — the toolbar
says why. An output table with nothing connected, or an output column the pipeline doesn't
fill, prices all the same and shows dashes (see [Workbench Output](nodes/workbench-output.md#filling-the-tables)). Ctrl+S still saves the project in Preview.

## The underwriter app
Underwriters use the sheets in an app of their own, apart from Haute: the sheets drawn as
Preview draws them, priced by the pipeline packed into the same app, with nothing of the
builder on screen. With a quote store it is where they manage their quotes too.

- `haute app serve` builds the app from the project as it is saved and serves it at
  `http://127.0.0.1:8001/`, signed in as your git identity, so you can see the sheets and
  the Quotes page as underwriters will before you merge. Only your machine can reach it, at
  `127.0.0.1` or `localhost`: the app refuses any other name for your machine. The
  quotes you save there are kept in `.haute/app/store.sqlite`, which a rebuild leaves as it
  is: its tables are your schema's (see [Reading quotes with SQL](#reading-quotes-with-sql)),
  brought to it as the app starts: a table or column you add or rename keeps every quote's
  values, and one you remove, or change while it holds values, is dropped or built again, the
  app naming each table or column whose values went. `haute app serve --reader` signs you in
  as an underwriter who may only read quotes.
- `haute app build` writes a **release** to `.haute/app/release` (or the folder you name):
  the pipeline, checked and bundled
  as `haute deploy` does it (the test quotes included), with the sheets and an `app.py` that
  serves them. The release's id changes when anything that prices a quote changes, and the
  app names it in its bar.
- Say where the app runs in `haute.toml`. Azure Container Apps is the host so far; its
  built-in authentication signs underwriters in, and the app refuses anyone it has not.
  `edit_role`, if you set it, is the app role that may change quotes: everyone else the
  host signs in reads them. Ask your IT team to give the app role to the underwriters who
  quote, and to pass roles to the app; the app refuses a request whose roles it cannot
  read rather than guess:

  ```toml
  [workbench.app]
  host = "azure-container-apps"
  edit_role = "Quotes.Edit"
  ```

- To build the app's image, pin its base image as the container target does. The release is
  then the image's build context, and `haute app build --image <tag>` builds it with Docker;
  the image installs the scoring runtime and the quote store's packages:

  ```toml
  [deploy.container]
  base_image = "python:3.11.9-slim"
  ```

- The hosted app keeps quotes in the database of an environment you declare in
  `haute.toml`, as your schema's tables. Declare each environment by name with its database's
  provider, `sqlite` so far; where the database is never goes in the file:

  ```toml
  [workbench.store.test]
  provider = "sqlite"
  ```

  The host names the environment in `HAUTE_ENVIRONMENT` and says where its database is in
  `HAUTE_STORE_DATABASE` (the file's path) or `HAUTE_STORE_URL` (a whole connection URL).
  Without an environment named the app keeps nothing, says so, and prices its one quote as
  `PREVIEW`, option 1. The app does not start on a database that does not hold its release's
  tables: `haute app migrate --environment test` brings the database to the release built in
  `.haute/app/release` (or the folder `--release` names) before that release starts. It adds
  and renames without losing a value, keeps a table or column you removed, listing it with
  the DROP statement for your IT team, and refuses, before it changes anything, a change to a
  column or table that holds values. `--plan` prints what it would do, `--sql` the SQL your IT
  team can run instead, and a change only a restore can undo needs `--restore-point`, naming
  the backup.
- The app prices four quotes at once; set `HAUTE_APP_PRICING_WORKERS` to change that. A
  pricing that runs past 60 seconds is answered as timed out but keeps its place until it
  ends, and while every place is taken a quote is refused as busy, to be priced again in a
  few seconds, rather than waiting.

A page opened before a new release is refused when it prices or saves, and offers
**Reload**, which picks up the release; its unsaved values stay in the browser, though an app
that keeps no quotes keeps nothing typed.

### Reading quotes with SQL
The app keeps each quote in your schema's own tables, so a report reads them with plain SQL.
Each table of the schema is a table of the same name, `quote_details` included, with the
schema's columns under their names: an Integer a 64-bit integer, a Decimal a
double-precision number, Text text, True/false a boolean and a Date a date. Every row holds
the quote's **quote_reference** and **quote_option**, and a row of a many-row table its
**quote_row** too: an input row's place on its sheet, blank rows counted, and an output
row's place in the pricing. Join the tables on **quote_reference** and **quote_option**:

```sql
SELECT d.broker, SUM(p.total_premium) AS premium
FROM quote_details AS d
JOIN premium AS p ON p.quote_reference = d.quote_reference
                 AND p.quote_option = d.quote_option
WHERE d.stage = 'final'
GROUP BY d.broker;
```

A table holds each quote as it was last saved: a save replaces the quote's rows, and the
output tables hold the price it was saved with, none when it was saved unpriced. A value a
Show when hid is saved blank, and an output that was not a number, such as infinity, is
saved empty. `quote_details` also holds what the app keeps about each quote:
**quote_version**, which every change moves on; **saved_at** and **saved_by**, the last
save; **priced_release**, the release that priced it; **source_reference**,
**source_option** and **source_relation**, the quote it was made from and how; and the
handover note with who wrote it and when. The schema shows them under each table, locked.
Two tables of the app's own sit beside yours: `haute_store`, which says what the database
holds, and `haute_activity`, a row for each thing done to a quote, who did it and when.

Names reach the database as the schema writes them, so the schema refuses a name the
database could not keep: two columns of a table whose names differ only in case, a name
longer than 63 characters, a name starting `haute_`, a column named `quote_row` in a
many-row table, and a table named starting `sqlite_`.

### The Quotes page
The app opens on the Quotes page. On the left are **My work** (the quotes with you),
**Team** and **Archived**, and the stages of the list showing, **Draft** and **Final**,
each with its count. Each quote shows its reference and
option (`ABC-1/2`), its quote name (or its insured, or its reference and option), and
its insured, broker and inception date under the labels the sheets give them, read from its
quote details. Search matches every word you type against the reference, however its
letters are cased, the quote name, insured and broker, and `ABC-1/2` finds that option
alone; in Team you can narrow the list to who a quote is with, and order it by when it was
saved, by **Reference** (A to Z, each reference's options in order) or by its insured. The
list, the search and the order are in the page's address, so a link or Back returns to the
same list. A row opens its quote; Duplicate and Archive appear as you hover over it, and
Archive can be undone from the message it shows. `/` jumps to the search.

**New quote** (or `N`) opens a window over the list asking for the quote's reference and
the quote details your underwriters type: its name, insured, broker, inception and expiry
dates and whether it is a renewal, labelled and checked as on the sheets, starting with the
columns' defaults. The reference is up to 40 characters, without `/`, which the app puts
before the option, and is not `.` or `..`. It is kept as you key it, so `ABC-1` and `abc-1`
are two references; a reference already in use is refused, naming the quote that holds it,
with **Open it**. A detail the analyst made required can be left empty here: the quote asks
for it as it is marked final. **Create** makes the quote, a **Draft** holding what you
keyed, and opens it; one left with nothing more typed stays a
**Draft** until archived. **Cancel** or `Escape` closes the window and makes nothing. If the
answer is lost while the quote is being made, the window offers **Try again**, which sends
exactly what it sent, and keeps it if you close it: the app offers it back, with **Try
again** or **Discard**, the next time it opens in that tab, so a quote is never made twice.

### A quote
A quote opens at its own address, under a bar holding its reference and option
(`ABC-1/2`), its name (its `quote_name`, saved with the next **Save**), its stage, and:

- **Save**. The quote is priced as you enter it, as Preview prices the sample, with no
  **Price** to press and no **Clear**, and a quote opened that can change is priced again
  when it was saved unpriced or priced on an earlier release. **Save** waits for that
  pricing and keeps the quote with its price, or unpriced when it could not be priced (the
  button says **Save unpriced** then, and the bar says why); the app prices it again as it
  saves, and says so if the price it keeps moved. A save replaces what the quote held, so
  duplicate a quote first to keep an offer as it stood. A save is refused only when a value
  is not of its column's type, naming each one; a required value left empty, or a value
  outside its range, saves.
- **Mark final** makes the quote the final quotation, once it is saved with a price of the
  running release and every column the analyst made required, input or output, holds a
  value; otherwise the app names each one that does not. A final quote cannot be saved until
  someone **amends** it, giving a reason, which takes it back to **Draft**: its next save
  replaces the final quotation.
- **More**: **Duplicate**, **Renew** and **Create alternative** each make a new quote from
  this one as it is saved, linked to it, and open it. **Duplicate** and **Renew** open the new
  quote's window over the quote, filled from it with the reference blank, a renewal with
  **is_renewal** ticked: key the new reference and **Create**; **Cancel** leaves the quote as
  it was. With changes to the quote not yet saved, the new quote is made and the app asks
  before leaving them; stay, and a message names the quote made. **Create alternative** makes the quote at once under the same reference as its
  next option (`ABC-1/2` from `ABC-1/1`);
  **Archive** takes it out of the lists (it opens read only until restored); **Copy link**.
- **History**: the handover note, the quotes it came from or that were made from it, and
  what was done to the quote, by whom and when, with the reasons given for amending it.

Beneath the bar are who saved it and when, the release that priced it, where the quote came
from, and who it is with, which you can change. A quote opens on the release's sheets; a row
whose value a later release no longer allows is named as no longer fitting. If a colleague
saves a quote while you have it open, your save is refused naming who did it, with your
values kept: open the newest, or save yours as a new quote, keying its reference in the
window filled from your values; it opens and prices as a duplicate does, and **Save** keeps
its price. If your save's answer was lost and a colleague saved the quote after it landed,
the app says your save was made and offers the newest. Values you have not saved are kept in
the browser and offered back when you open the quote again, and leaving with them asks
first. `Ctrl+S` saves.
