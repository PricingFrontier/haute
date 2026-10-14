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
toolbar keeps Haute's project controls (Assistant, Help, the branch, **Save** and
**Commit**) and shows the workbench's own: **Build** over **Preview**, then, while building,
**Sheets** over **Schema** and **Display**, in Preview **Test quotes**, then **Undo** over
**Redo**, and **Zoom In** over **Zoom Out** on the sheets.

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

A column's **default** is the value a new quote starts with in it, as if the underwriter
had typed it: ticked or not for a True/false, one of its allowed values when it has some,
a date for a Date, or typed. Only a one-row input table's columns take one. Build shows a
new quote with its defaults, and the underwriter app's **New quote** opens with them; the
sample you type in Preview, and a quote made from another quote, keep their own values. A
quote holding nothing but its defaults is not priced, marked or saved until something is
typed, and a value typed equal to its default counts as the default.

- Enter in a column's name adds the next column; Alt+Up and Alt+Down move one.
- **Add index** on a many-row table adds a column first in the table, `row_number` until
  renamed, that numbers the rows from 1. It is an Integer and can be the table's key.
- The editor says what would stop the schema being the pipeline's tables: a table's name is
  held to the rules a Quote Input's labels follow (an identifier, no reserved name, unique
  whatever its case), a column's name is an identifier that is not a Python keyword, unique
  in its table, a many-row table needs a key, an input column's range must not be inverted
  and its allowed values must be of its type, a True/false or Date column can have none,
  and a default must be a value the column takes (of its type, in its range, one of its
  allowed values). Changing a column's type drops the rules and the format the new type
  cannot have, and the default when it becomes or stops being True/false, or becomes a Date
  while it is not a date; switching a
  table to many rows clears its defaults.
- Removing a column or a table a sheet shows asks first, and takes it off the sheet.

### Quote details

Every workbench has Haute's own table, `quote_details`, first in the Schema section: one
row of a quote's details, which the underwriter app keeps with each saved quote and lists
on its Quotes page. Its columns are fixed: **reference**, **quote_name**, **insured**,
**broker**, **inception_date**, **expiry_date**, **valid_until**, **currency**, **stage**,
**assignee** and **archived**. Put them on the sheets as any other columns, and give them
labels, groups, help, placeholders, defaults and rules; the pipeline receives the table as
it receives any other input, so a rule can read a quote's inception date or its stage.

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
[Showing sheets, sections and components conditionally](#showing-sheets-sections-and-components-conditionally)).
The palette on the left offers four components and a section; drag a component onto the
sheet (a ghost shows where it lands) and it goes into the section under the top of the
ghost:

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
[Showing sheets, sections and components conditionally](#showing-sheets-sections-and-components-conditionally)), its layout (the rows a
Table starts with, whether they are **fixed**, with no Add row or delete, and whether it is
**transposed**, or a Collection's columns across), a Table's **look** (compact or comfortable rows; gridlines between the rows, a full
grid or none; a header as written or in capitals; row numbers; **groups**; striped rows;
a frame or none), a Collection's **look** (labels above or beside the boxes; compact or comfortable
boxes; a frame or none), the **order** of the fields it shows (drag, or
Alt+Up and Alt+Down), and its **fields**, ticked from the schema tables of its kind. A
Chart's settings are its **Type** (Column, Line, Area or Pie), **Rows** (what labels the
rows of many-row tables, or Row number), its **Legend**, and whether it is **Stacked**,
**Horizontal** and shows **Data labels**; its fields come from any table, number columns
other than an index. A grid holds one kind of row, so once a Table or a Chart shows a
field, tables whose rows do not line up with it (keyed differently) are greyed out; an
input table and an output table with the same key line up, so each row can show its
premium. Output columns are shaded. A transposed Table turns its grid on its side: each
field is a line, labelled at its left, and each row is a column, numbered along the top,
with **+** at the end of the numbers to add one and a delete over each number; its stripes
and gridlines follow the lines as drawn. With **groups** ticked, the columns side by side
in one schema group share a heading over their labels, as merged cells over a table's
headers would in Excel; a column in no group has none. Transposed, the groups sit in a
column before the labels. Order the fields so each group's columns are together. A
Table's and a Collection's properties are in two
panes: **Config**, the fields it shows and their order, and **Display**, its title, when it
shows, its layout and its look. A Heading's properties are its **Text** and
**Description**, when it shows, its **Size** and its **Align**ment.

Drag a component to move it and any of its eight handles to resize it; everything snaps
to the grid. A component stays inside its section: it stops at the section's edges, and
moves into another section only once its top is over it. Resizing a component past its
section's bottom pushes the section's border down. It stays inside the sheet's width
too, stopping at its edges; resizing it past the right edge widens the sheet. With a component selected (click it, or Tab to it), the
arrow keys nudge it (Shift for a pixel), Ctrl+D duplicates it, Delete or Backspace removes
it and Escape deselects it; Ctrl+Z and Ctrl+Y undo and redo. Ctrl+1 fits the sheet to the
window, and the zoom runs from 25% to 200%. A component with a problem — no fields, a
Heading with no text, a column no longer in the schema, a table of the other kind, a
Chart's field that is not a number or is an index, tables whose rows do not line up, a pie
of many rows with more than one field — gets a dashed frame that names the problem. Tab,
and a screen reader, go through the components as they sit on the sheet, section by
section, then row by row from the top and left to right along each row, whatever order you
added them in.

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

Sheets have tabs along the top: **+** adds one, double-click (or F2) renames one, dragging
a tab onto another moves its sheet there (or press Alt+Left and Alt+Right on a tab), the
settings mark on the showing tab opens the sheet's own panel (its title, when it shows
and its width),
and the cross beside it deletes it (asking first when components are on it; the schema and
the sample stay).

## Showing sheets, sections and components conditionally

A sheet, a section or a component can show only when the quote calls for it: a business
interruption sheet only while its cover is ticked, a flood section only while flood is
ticked, a referral note only while the sum insured is above a limit. Set it under **Show
when**, in a component's properties or in a sheet's or a section's own panel. **Add
condition** tests a column of a one-row input table:

- a tick box **is ticked** or **is not ticked**;
- any other column **is filled** or **is empty**, and **equals** or **does not equal** a
  value;
- a text or number column **is one of** or **is not one of** some values, ticked from its
  allowed values or typed comma-separated;
- a number **is greater than**, **is at least**, **is less than** or **is at most** a
  value, and a date **is after**, **is on or after**, **is before** or **is on or before**
  one.

With two or more conditions, choose whether **all** or **any** of them must hold. A
section on a hidden sheet is hidden too, as is a component in a hidden section, and a sheet
or a section waiting on a box that sits somewhere hidden is hidden with it, so a cover and
its extensions hide and show in a chain.

While building, a new quote decides, holding its defaults alone: a hidden sheet's tab, a hidden
section and a hidden component are faded, their eye struck through (hover it to read the
conditions), and you can still select and edit them; every section keeps its place. In
Preview the sample decides as you type it, and in the underwriter app the quote: a hidden
sheet has no tab; a hidden section is not drawn and the
sections under it move up to close the gap; and a hidden component is not drawn, its space
in its section left empty, since components are placed freely. So give what should close
up a section of its own, with its Heading in it, and put the tick box that controls it
just above it, where the underwriter is working. While the underwriter holds a mouse
button down on the sheet nothing moves, so a click is never pulled from under them, and
when a section above them opens or closes the sheet scrolls to keep their place.

A quote is what the sheets show. Values typed into hidden cells are left out of what is
priced, as if they had never been typed, for the sample priced in Preview (and the
Workbench Input's copy of it) and for a quote in the underwriter app, and a hidden required
cell is never marked. The values are kept, so ticking the cover again brings them back. A Chart takes
nothing typed, so a column only a Chart plots never hides and a condition on it never
holds, and a value only a hidden component takes is left out even when a Chart that shows
plots it. A Heading takes nothing either; it just hides and shows. A condition
that cannot work (its column gone or retyped, a missing value) and a sheet or a section
nothing could show again once hidden (its tick box only inside it) are named on the
sheet's tab, the section's header or the component's dashed frame.

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
sits in its section, with no section bars and nothing to move or lay out. It is where you
type the **sample**, a quote you key in as an underwriter would: it is saved with the
sheets, and it is what the [Workbench Input](nodes/workbench-input.md)'s previews run on
once saved.

A Collection's boxes hold its tables' one row, and a Table's grid holds its tables' rows,
with **Add row** and a delete on each row unless its rows are fixed (they add and delete
the row in every input table the grid shows, so the cells stay side by side). A Table
starts with its rows, empty; the first value you type keeps them, and from then on the
rows are yours to add and delete, fewer than it started with too, though a Table of fixed
rows never shows fewer. Change a Table's rows while building and Preview shows it: empty
rows after the last one you typed into go, or more are added. Each cell is the control its
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
Chart plots the sample with what pricing it gave. There is no **Price** to press and no
**Clear**: a sample with nothing typed is not priced, and its outputs show dashes. The
sample is priced whatever its cells hold. Once anything is typed, a cell that breaks its
column's rules (a required cell left empty, a number that is not one, a whole number that
is not whole, a date that is not one, a number outside its range, a value that is not one of the allowed ones) is
outlined, naming the problem when you hover, and priced all the same. When pricing fails —
no Workbench Output on the pipeline, a table with nothing connected, a value that does not
fit its column — the toolbar says why. Ctrl+S still saves the project in Preview.

## Test quotes

Keep the quotes you check rates with as **test quotes**. In Preview, **Test quotes** opens
a panel beside the sheets. Type a quote into the sheets, the sample, name it in the panel
and press **Save quote**: Haute prices it in full on the pipeline as it is now, whatever its
cells hold, and keeps it with its prices, in `tests/quotes/test_quotes.sqlite` (in the folder
`[test_quotes] dir` in `haute.toml` names). Save and Commit carry the file with the rest of
the project.

- **Open** puts a test quote on the sheets in place of the sample (**Undo** brings the
  sample back). Its values follow the tables and columns
  they were typed into, renamed or not; the panel names any value that has no place on
  the sheets now.
- **Run all** (or **Run ticked**) prices each test quote on the pipeline as it is when you
  press it, and compares what comes out with the prices you accepted for it: each shows
  **Unchanged**, **Changed** or **Failed**. Click a changed quote to see every output that
  moved, the accepted value beside the new. Change the pipeline or the sheets while a
  run's results are showing and the panel says to run again.
- **Accept** (the tick) keeps a changed quote's new prices as the ones to expect, when the
  change was meant. **Delete** (the bin) removes a test quote, after asking.

When the project deploys, the dry run prices every test quote again and fails on any
output that moved and was not accepted, so a rate change arrives with its test quotes
accepted. On a pull request, a project set up with `haute init --workbench` reports what
the change does to the test quotes: GitHub posts the report on the pull request, and
GitLab and Azure DevOps keep it with the run. `haute test-quotes report` writes the same
report on your machine.

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
  that needs it starts.

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
  the revision showing, linked to it; **Archive** takes it out of the lists (it opens read
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
