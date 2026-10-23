# Workbench

The workbench is where a pricing team lays out the quote an underwriter keys in: the
**schema** of typed tables the pipeline reads, and the **sheets**, each a stack of
**sections** holding Tables and Collections that show those tables' columns, Charts that
plot them and Headings that name them. It is part of Haute, switched
on per project, and what it holds lives in a file in the project, `forms/form.json`,
versioned beside the pipeline.

!!! note "Enabling it"
    Add the table to `haute.toml` (`haute init --workbench` writes it for a new project):

    ```toml
    [workbench]
    enabled = true
    form = "forms/form.json"   # optional; this is the default
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
Help, the branch, **Save** and **Commit**) and shows the workbench's own: **Build** over
**Preview**, **Sheets** over **Schema**, **Display**, **Undo** over **Redo**, and **Zoom
In** over **Zoom Out**. They stay in place in both modes: Sheets, Schema and Display are
greyed out in Preview, and the zoom while the schema shows.

**Save** and Ctrl+S save the project, the same from either view: `forms/form.json` first
when the workbench has unsaved edits, then the pipeline, with the Workbench Input's and
Workbench Output's tables brought up to date in between, and each file's save is recorded
on your branch's save ledger, so it shows in the Git panel's history. **Commit** records a
milestone of the whole project after that save. A save is refused when the form file
changed on disk since the workbench read it (an edit by hand, or a branch switched outside
Haute), and then the pipeline is not saved either; the banner's **Reload** reads the file
again, dropping your unsaved edits. When the form file changes on disk (an edit by hand, a
pull, or a branch switched), the workbench follows it, unless you have unsaved edits, when
the banner appears instead. A form whose file leaves a component below its section or past its
sheet's edge, as a merge or an edit by hand can, opens with that section or sheet grown
to hold it, nothing moved: a notice says what grew, and the next save keeps it.

Both workbench nodes' panels have **Edit in Workbench**, which opens the view.

## Schema

The tables the sheets work with, and the pipeline's tables. Each table has a name, a role —
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
values. A number shows in its format wherever the sheets show it, a percentage as the
value times 100, so you type 0.125 for 12.5%; while you type into a cell it shows as
typed, and what is typed is what is saved and priced.

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
new quote with its defaults, and the underwriter app's **New quote** opens with them; the
sample you type in Preview, and a quote made from another quote, keep their own values. A
quote holding nothing but its defaults is not priced, marked or saved until something is
typed, and a value typed equal to its default counts as the default.

- Enter in the last column's name adds the next column; in any other column's name it just
  keeps what you typed. A column added, by Enter or **Add column**, takes the type of the
  column above it: Text when it is the table's first or comes under the index.
- Drag a column by the grip at its left, or press Alt+Up and Alt+Down in its name, to move
  it within its table. Drag a table by the grip beside its name, or press Alt+Up and
  Alt+Down in its name, to move the table. A grip shows while the pointer is over its row.
- **Add index** on a many-row table adds a column first in the table, `row_number` until
  renamed, that numbers the rows from 1. It is an Integer and can be the table's key.
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

### Quote details

Every workbench has Haute's own table, `quote_details`, first in the Schema section, where
it stays: one row of a quote's details, which the underwriter app keeps with each saved quote and lists
on its Quotes page. Its columns are fixed: **reference**, **quote_name**, **insured**,
**broker**, **inception_date**, **expiry_date**, **valid_until**, **currency**, **stage**,
**assignee**, **archived** and **is_renewal**. Put them on the sheets as any other columns, and give them
labels, groups, help, placeholders, defaults and rules; the pipeline receives the table as
it receives any other input, so a rule can read a quote's inception date or its stage.
**is_renewal** is a tick box: a quote made with the app's **Renew** opens with it ticked,
and an underwriter ticks it for a renewal started any other way. A project saved before
**is_renewal** existed takes it on when it is opened; save once so the Workbench Input
copies it.

The app sets **reference**, **stage**, **assignee** and **archived** itself: their cells take
no typing in the app and take no rules and no default. Assigning, archiving, restoring, marking final and
reopening a quote each save a new revision with those details changed and its values and
price kept, so the history shows when a quote moved; the pipeline sees the new details the
next time the quote is priced. A new quote is priced before it takes its reference, so its
first price is made without one.

A table or a column you add reaches the pipeline when you save: the editor fetches the
tables again, the Workbench Input's and Workbench Output's tables follow the schema, and
the pipeline is saved with them in the same save. Until then it is on the sheets alone.

## Sheets

A sheet is a canvas on a snap grid with a set **width** (1280 pixels to start, and a
sheet you add as wide as the last), set in the sheet's own panel, and as long as its
sections. It stays beside the palette and under the sheet tabs: zoom out and it is drawn
smaller from that corner; wider than the window, it scrolls across.
It is a stack of **sections**: strips across its whole width, one under the next, each
holding the components placed in it. A new sheet is one section; hide a section and, in
Preview, the sections under it move up to close the gap (see
[Showing sheets, sections, components and fields conditionally](#showing-sheets-sections-components-and-fields-conditionally)).
The palette on the left offers four components and a section; drag a component onto the
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
Alt+Up and Alt+Down), and its **fields**, ticked from the schema tables of its kind. A
Chart's settings are its **Type** (Column, Line, Area or Pie), **Rows** (what labels the
rows of many-row tables, or Row number), its **Legend**, and whether it is **Stacked**,
**Horizontal** and shows **Data labels**; its fields come from any table, number columns
other than an index. A grid holds one kind of row, so once a Table or a Chart shows a
field, tables whose rows do not line up with it (keyed differently) are greyed out; an
input table and an output table with the same key line up, so each row can show its
premium. Output columns are shaded. A transposed Table turns its grid on its side: each
field is a line, labelled at its left, and each row is a column, numbered along the top,
with a delete over each number; its stripes
and gridlines follow the lines as drawn. With **groups** ticked, the columns side by side
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
of many rows with more than one field — gets a dashed frame that names the problem. Tab,
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
  values' size, a pixel smaller in capitals), their **Direction**, a **Fill** and **Font**
  colour for the whole header, **Bold**, and **Wrap**. **Rotated up** and **Rotated down**
  turn the labels to run up or down their column, and **Stacked** puts their letters one
  under another; turned labels sit at the foot of the header, so set narrow column widths
  to fit more columns across. Wrapped labels break between words onto more lines rather
  than being cut off; a turned label that wraps starts a new line beside itself. Group
  names stay level.
- **Lines**: which **Gridlines** to draw (between rows, a full grid or none), their
  **Style** (solid, dashed or dotted), **Weight** (thin, medium or thick) and **Colour**.
  The weight and colour also apply to the rule under the header and the line before the
  output columns, which stay solid.
- **Frame**: whether it is framed and, if so, the frame's **Weight** and **Colour** (the
  rule under its title takes the colour too).
- **Columns**: click a column to open its own look: its header's **Fill**, **Font** and
  **Align** (Auto sits the header as its cells sit, though a transposed Table's label sits
  at its start while its cells' Align is Auto too), its cells' **Fill**, **Font** and
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
- a text or number column **is one of** or **is not one of** some values, ticked from its
  allowed values or typed comma-separated;
- a number **is greater than**, **is at least**, **is less than** or **is at most** a
  value, and a date **is after**, **is on or after**, **is before** or **is on or before**
  one.

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
keeps its place, and every field its place in its component. In
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
priced, as if they had never been typed, for the sample priced in Preview (and the
Workbench Input's copy of it) and for a quote in the underwriter app, and a hidden required
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
Below**, the last two asking how many rows to insert, and **Delete Row** deletes it, as
does the delete at the end of each row. On a transposed Table, right-click a column, or
its number, and the menu inserts columns to the left or right. Rows are inserted and
deleted in every input table the grid shows, so the cells stay side by side. A Table
starts with its rows, empty; the first value you type keeps them, and from then on the
rows are yours to insert and delete, fewer than it started with too, though a Table of fixed
rows never shows fewer. Change a Table's rows while building and Preview shows it: empty
rows after the last one you typed into go, or more are added. A Table showing a table that
has a row for each value (see [Schema](#schema)) shows those rows whatever its own rows
say. Each cell is the control its
column calls for: a tick box, a dropdown of the allowed values, a date picker, or a text
field. An empty cell is blank, or shows its column's placeholder. The sample is part of
what **Save** writes, and **Undo** and **Redo** take back what you type, in Preview as
while building.

The sample is **priced live**: 300ms after the schema or the sample changes, a value
counting once it is entered (press Enter or Tab, or leave a text or number cell; a tick,
a choice or a date counts as you make it), and each time you open Preview, Haute prices it on the pipeline open in the
editor, as it stands, saved or not, exactly as a deployed pipeline would price it. Every
output column shows its value, a Collection's its table's one row and a Table's the row in
the answer keyed like each grid row, dimmed while a newer answer is on its way, and every
Chart plots the sample with what pricing it gave. A Table of output columns alone, such as
a premium by peril, shows a row for each row of the answer, as a Chart of them does; until
it is priced (or while its table has nothing connected) it shows its starting rows of
dashes. There is no **Price** to press and no
**Clear**: a sample with nothing typed is not priced, and its outputs show dashes. The
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
  the Quotes page as underwriters will before you merge. Only your machine can reach it. The
  quotes you save there are kept in `.haute/app/quotes.sqlite`, which a rebuild leaves as it
  is; `haute app serve --reader` signs you in as an underwriter who may only read them.
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

- The hosted app keeps quotes in the SQL database your IT team provides. The host gives the
  app its SQLAlchemy URL in `HAUTE_QUOTE_STORE_URL`; without it the app prices and keeps
  nothing, and says so. The app does not start on a database it has not been migrated to:
  run `haute app migrate` (with `HAUTE_QUOTE_STORE_URL` set, or `--url`) before a release
  that needs it starts. Back the database up first: after some migrations, such as the one
  that adds **is_renewal**, an earlier release no longer starts on the database, so rolling
  back means restoring that backup.

A page opened before a new release is refused when it prices or saves, and reloading it
picks up the release; its unsaved values stay in the browser.

### The Quotes page
The app opens on the Quotes page. On the left are **My work** (the quotes with you),
**Team** and **Archived**, and the stages of the list showing, **Draft** (saved without a
price), **Priced** and **Final**, each with its count. Each quote shows its reference, its
quote name (or its insured, or its reference), and its insured, broker and inception date
under the labels the sheets give them, read from its quote details. Search matches every
word you type against the reference, quote name, insured and broker; in Team you can
narrow the list to who a quote is with, and order it by when it was saved, its reference
or its insured. The list, the search and the order are in the page's address, so a link or Back
returns to the same list. A row opens its quote; Duplicate and Archive appear as you hover
over it, and Archive can be undone from the message it shows. **New quote** (or `N`) starts
a new one, holding the columns' defaults; `/` jumps to the search.

### A quote
A quote opens at its own address, under a bar holding its reference (`Q-000123`), its
name (its `quote_name`, saved with the next **Save**), its stage, and:

- **Save**. The quote is priced as you enter it, as Preview prices the sample, with no
  **Price** to press and no **Clear**, and a quote opened that can change is priced again
  when it was saved unpriced or priced on an earlier release. **Save** waits for that
  pricing and keeps the quote with its price, or unpriced when it could not be priced (the
  button says **Save unpriced** then, and the bar says why); the app prices it again as it
  saves, and says so if the price it keeps moved. Each save is a new revision, and the
  quote's first save gives it its reference.
- **Mark final** pins the newest revision as the final quotation, once it is priced on the
  running release; a final quote cannot be saved over until someone **reopens** it, giving a
  reason. The final stays in its history.
- **More**: **Duplicate**, **Renew** and **Create alternative** each start a new quote from
  the revision showing, linked to it, a renewal with **is_renewal** ticked; **Archive** takes it out of the lists (it opens read
  only until restored); **Copy link**.
- **History**: the handover note, every revision (each opens as it was saved), what was done
  to the quote, by whom and when, and the quotes it came from or that were made from it.

Beneath the bar are the revision, who saved it and when, the release that priced it, where
the quote came from, and who it is with, which you can change. A quote saved on sheets a
later release changed opens on the sheets it was keyed into, read only; **Move to current
sheets** brings its values onto today's, naming any that no longer fit. If a colleague saves
a quote while you have it open, your save is refused naming who did it, with your values
kept: open the newest, or save yours as a new quote. Values you have not saved are kept in
the browser and offered back when you open the quote again, and leaving with them asks
first. `Ctrl+S` saves.
