# Workbench

The Workbench is where a pricing team lays out the quote an underwriter keys in: the
**schema** of typed tables the pipeline reads, and the **sheets** of Tables and Collections
that show those tables' columns. It is part of Haute, switched on per project, and what it
holds lives in a file in the project, `forms/form.json`, versioned beside the pipeline.

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

**Workbench** at the bottom of the node palette shows the Workbench over the pipeline
editor; **Pricing** brings the pipeline back. The pipeline editor keeps everything it had
(its document, its undo history, live sync) while the Workbench shows; its keyboard
shortcuts are off meanwhile, so nothing you press in the Workbench edits the pipeline. The
toolbar keeps Haute's project controls (Assistant, Help, the branch and **Save**) and shows
the Workbench's own: **Sheets** over **Schema**, **Undo** over **Redo**, and **Zoom In**
over **Zoom Out** on the sheets.

**Save** and Ctrl+S write `forms/form.json`. There is no **Commit** in the Workbench yet:
the Git panel's own Commit still records the pipeline. A save is refused when the file
changed on disk since the Workbench read it (a branch switch, or an edit by hand); the
banner's **Reload** reads it again, dropping your unsaved edits.

Both workbench nodes' panels have **Edit in Workbench**, which opens the view.

## Schema

The tables the sheets work with, and the pipeline's tables. Each table has a name, a role —
**Input**, sent to the pricing engine, or **Output**, returned by it — and its rows: **One
row** per quote, such as policy details, or **Many rows**, such as an equipment schedule.
Each column has a type (Text, Integer, Decimal, True/false or Date, drawn as the step editor
draws a value of that kind and changed from its marker), a name and, in a many-row table,
whether it is a **key**, part of what says which row is which. **Label and rules** beside a
column opens its label and, in an input table, the rules an underwriter's value must meet:
required, a range for a number, the allowed values.

- Enter in a column's name adds the next column; Alt+Up and Alt+Down move one.
- **Add index** on a many-row table adds a column first in the table, `row_number` until
  renamed, that numbers the rows from 1. It is an Integer and can be the table's key.
- The editor says what would stop the schema being the pipeline's tables: a table's name is
  held to the rules a Quote Input's labels follow (an identifier, no reserved name, unique
  whatever its case), a column's name is an identifier unique in its table, a many-row table
  needs a key, and an input column's range must not be inverted and its allowed values must
  be of its type.
- Removing a column or a table a sheet shows asks first, and takes it off the sheet.

When the schema is saved, the editor fetches the tables again and the Workbench Input's
ports follow it, so the pipeline has changes to save, as after any edit.

## Sheets

A sheet is a canvas on a snap grid that fills its width and grows to hold what is on it.
The palette on the left offers two components; drag one onto the sheet (a ghost shows
where it lands):

- A **Table** shows columns of many-row tables as a grid of at least its number of rows.
- A **Collection** shows columns of one-row tables as boxes with their labels above, its
  number of boxes across.

Select a component to open its properties on the right: its title, its layout (a Table's
rows, a Collection's columns across), the **order** of the fields it shows (drag, or Alt+Up
and Alt+Down), and its **fields**, ticked from the schema tables of its kind. A grid holds
one kind of row, so once a Table shows a field, tables whose rows do not line up with it
(keyed differently) are greyed out; an input table and an output table with the same key
line up, so each row can show its premium. Output columns are shaded.

Drag a component to move it and any of its eight handles to resize it; everything snaps to
the grid. With a component selected, the arrow keys nudge it (Shift for a pixel), Ctrl+D
duplicates it, Delete removes it and Escape deselects it. Ctrl+1 fits the sheet to the
window. A component with a problem — no fields, a column no longer in the schema, a table
of the other kind — gets a dashed frame that names the problem.

Sheets have tabs along the top: **+** adds one, double-click renames one, and the cross on
the showing tab deletes it (asking first when components are on it; the schema and the
sample stay).
