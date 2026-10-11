# Workbench

The workbench is where a pricing team lays out the quote an underwriter keys in: the
**schema** of typed tables the pipeline reads, and the **sheets** of Tables and Collections
that show those tables' columns and Charts that plot them, with Headings naming their
sections. It is part of Haute, switched
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
toolbar keeps Haute's project controls (Assistant, Help, the branch, **Save** and
**Commit**) and shows the workbench's own: **Build** over **Preview**, then, while building,
**Sheets** over **Schema**, **Undo** over **Redo**, and **Zoom In** over **Zoom Out** on
the sheets.

**Save** and Ctrl+S save the project, the same from either view: `forms/form.json` first
when the workbench has unsaved edits, then the pipeline, with the Workbench Input's and
Workbench Output's tables brought up to date in between, and each file's save is recorded
on your branch's save ledger, so it shows in the Git panel's history. **Commit** records a
milestone of the whole project after that save. A save is refused when the form file
changed on disk since the workbench read it (an edit by hand, or a branch switched outside
Haute), and then the pipeline is not saved either; the banner's **Reload** reads the file
again, dropping your unsaved edits. When the pipeline is read again from disk and the file
changed too, the workbench follows it, unless you have unsaved edits, when the banner
appears instead.

Both workbench nodes' panels have **Edit in Workbench**, which opens the view.

## Schema

The tables the sheets work with, and the pipeline's tables. Each table has a name, a role —
**Input**, sent to the pricing engine, or **Output**, returned by it — and its rows: **One
row** per quote, such as policy details, or **Many rows**, such as an equipment schedule.
Each column has a type (Text, Integer, Decimal, True/false or Date, drawn as the step editor
draws a value of that kind and changed from its marker), a name and, in a many-row table,
whether it is a **key**, part of what says which row is which. **Label, help, rules and
format** beside a column opens its details: its label; its **help**, shown as a small mark
beside the label on the sheets; for an Integer or a Decimal, its **format** (General,
Number, Percentage or a currency), decimal places and thousands separators, as the Explore
pivot offers them; and, in an input table, its **placeholder**, what an empty cell shows
(any type but True/false and Date), and the rules an underwriter's value must meet:
required, a range for a number, the allowed values. A number shows in its format wherever
the sheets show it, a percentage as the value times 100, so you type 0.125 for 12.5%;
while you type into a cell it shows as typed, and what is typed is what is saved and priced.

- Enter in a column's name adds the next column; Alt+Up and Alt+Down move one.
- **Add index** on a many-row table adds a column first in the table, `row_number` until
  renamed, that numbers the rows from 1. It is an Integer and can be the table's key.
- The editor says what would stop the schema being the pipeline's tables: a table's name is
  held to the rules a Quote Input's labels follow (an identifier, no reserved name, unique
  whatever its case), a column's name is an identifier that is not a Python keyword, unique
  in its table, a many-row table needs a key, an input column's range must not be inverted
  and its allowed values must be of its type, and a True/false or Date column can have none.
  Changing a column's type drops the rules and the format the new type cannot have.
- Removing a column or a table a sheet shows asks first, and takes it off the sheet.

A table or a column you add reaches the pipeline when you save: the editor fetches the
tables again, the Workbench Input's and Workbench Output's tables follow the schema, and
the pipeline is saved with them in the same save. Until then it is on the sheets alone.

## Sheets

A sheet is a canvas on a snap grid that fills its width and grows to hold what is on it.
The palette on the left offers four components; drag one onto the sheet (a ghost shows
where it lands):

- A **Table** shows columns of many-row tables as a grid of at least its number of rows.
- A **Collection** shows columns of one-row tables as boxes with their labels above, its
  number of boxes across.
- A **Chart** plots number columns, typed or priced. Of one-row tables it gives a point per
  field; of many-row tables, a series per field over the rows, each row labelled by a
  column you choose or numbered.
- A **Heading** names a section of the sheet, or the sheet itself, in text on the sheet:
  **Large** for a title, **Medium** for a section's heading, **Small** for a label, with an
  optional description under it saying what the section is for, aligned left, centre or
  right. It holds no values; give it the conditions of the section it names and it hides
  with it.

Select a component to open its properties on the right: its title, when it shows (see
[Showing sheets and components conditionally](#showing-sheets-and-components-conditionally)), its layout (a Table's
rows and whether they are **fixed**, with no Add row or delete, or a Collection's columns
across), a Table's **look** (compact or comfortable rows; gridlines between the rows, a full
grid or none; row numbers; striped rows), the **order** of the fields it shows (drag, or
Alt+Up and Alt+Down), and its **fields**, ticked from the schema tables of its kind. A
Chart's settings are its **Type** (Column, Line, Area or Pie), **Rows** (what labels the
rows of many-row tables, or Row number), its **Legend**, and whether it is **Stacked**,
**Horizontal** and shows **Data labels**; its fields come from any table, number columns
other than an index. A grid holds one kind of row, so once a Table or a Chart shows a
field, tables whose rows do not line up with it (keyed differently) are greyed out; an
input table and an output table with the same key line up, so each row can show its
premium. Output columns are shaded. A Heading's properties are its **Text** and
**Description**, when it shows, its **Size** and its **Align**ment.

Drag a component to move it and any of its eight handles to resize it; everything snaps to
the grid. With a component selected (click it, or Tab to it), the arrow keys nudge it
(Shift for a pixel), Ctrl+D duplicates it, Delete or Backspace removes it and Escape
deselects it; Ctrl+Z and Ctrl+Y undo and redo. Ctrl+1 fits the sheet to the window, and
the zoom runs from 25% to 200%. A component with a problem — no fields, a Heading with no text, a column no longer in the schema, a table
of the other kind, a Chart's field that is not a number or is an index, tables whose rows
do not line up, a pie of many rows with more than one field — gets a dashed frame that
names the problem. Tab, and a screen reader, go through the components as they sit on the
sheet, row by row from the top and left to right along each row, whatever order you added
them in.

Sheets have tabs along the top: **+** adds one, double-click (or F2) renames one, dragging
a tab onto another moves its sheet there (or press Alt+Left and Alt+Right on a tab), the
settings mark on the showing tab opens the sheet's own panel (its title and when it shows),
and the cross beside it deletes it (asking first when components are on it; the schema and
the sample stay).

## Showing sheets and components conditionally

A sheet or a component can show only when the quote calls for it: a business interruption
sheet only while its cover is ticked, a fleet schedule only while the class of business is
Fleet, a referral note only while the sum insured is above a limit. Set it under **Show
when**, in a component's properties or in a sheet's own panel. **Add condition** tests a
column of a one-row input table:

- a tick box **is ticked** or **is not ticked**;
- any other column **is filled** or **is empty**, and **equals** or **does not equal** a
  value;
- a text or number column **is one of** or **is not one of** some values, ticked from its
  allowed values or typed comma-separated;
- a number **is greater than**, **is at least**, **is less than** or **is at most** a
  value, and a date **is after**, **is on or after**, **is before** or **is on or before**
  one.

With two or more conditions, choose whether **all** or **any** of them must hold. A
component on a hidden sheet is hidden too, and a sheet waiting on a box that sits on a
hidden sheet is hidden with it, so a cover and its extensions hide and show in a chain.

While building, the sample decides: a hidden sheet's tab and a hidden component are faded,
their eye struck through (hover it to read the conditions), and you can still select and
edit them. In Preview the quote decides as it is typed: a hidden sheet has no tab, and a
hidden component is not drawn; its space stays empty, since components are placed freely.

A quote is what the sheets show. Values typed into hidden cells are left out of what is
priced, as if they had never been typed, for the sample priced live (and the Workbench
Input's copy of it) and for the quote in Preview, and a hidden required cell never stops
**Price**. The values are kept, so ticking the cover again brings them back. A Chart takes
nothing typed, so a column only a Chart plots never hides and a condition on it never
holds, and a value only a hidden component takes is left out even when a Chart that shows
plots it. A Heading takes nothing either; it just hides and shows. A condition
that cannot work (its column gone or retyped, a missing value) and a sheet nothing could
show again once hidden (its tick box only on that very sheet) are named on the sheet's tab
or the component's dashed frame.

## The sample

Type a sample quote straight into the components while building: a Collection's boxes
hold its tables' one row, and a Table's grid holds its tables' rows, with **Add row** and a
delete on each row unless its rows are fixed (they add and delete the row in every input
table the grid shows, so the cells stay side by side). Each cell is the control its column
calls for: a tick box, a dropdown of the allowed values, a date picker, or a text field. An
empty cell is blank, or shows its column's placeholder. The sample is part of what
**Save** writes, undone like any other edit, and it is what the
[Workbench Input](nodes/workbench-input.md)'s previews run on once saved.

The sample is also **priced live**: whenever the schema or the sample changes and typing
pauses, Haute prices it on the pipeline open in the editor, as it stands, saved or not, and
a Collection's output columns show what the pipeline gave them, dimmed while a newer
answer is on its way. When pricing fails — no Workbench Output on the pipeline, a table
with nothing connected, a value that does not fit its column — the toolbar says why. A
Table's output columns show the row in the answer keyed like each grid row. A Chart plots
the sample, with what pricing it gave.

## Preview

**Preview** shows the sheets as an underwriter uses them: the same components at the same
positions, with nothing to move or lay out. Type a quote into the cells, kept apart from
the sample (it is never saved, and stays while you switch to Build and back), and press
**Price**, enabled once something is typed into the tables the schema has, in cells the
sheets show. Haute first checks every cell the sheets show against its column's rules: a required cell left
empty, a number that is not one, a whole number that is not whole, a number outside its
range, a value that is not one of the allowed ones. A cell that breaks a rule is outlined,
naming the problem when you hover, and the toolbar says how many need attention; the marks
follow the cells as you fix them. A quote that passes is priced on the pipeline open in the
editor, and every output column shows its value: a Collection's its table's one row, and a
Table's the row keyed like each grid row, and every Chart plots the quote. The values and
the Charts dim when you change the quote, until
you press **Price** again, and the quote is priced exactly as a deployed pipeline would price
it. **Clear** empties the quote, after asking. Ctrl+S still saves the project in Preview.
