# Frontend node editors roadmap

## Scope

The node editors' panels, as [the frontend node editors
specification](../frontend-node-editors/high-level.md) describes them: which
answer a picker shows when two of its requests overlap, and what a field
accepts, saves and undoes. `FEN-01` comes from the review plan's browser
sweep of 10 November 2026 and was reproduced in a real browser, with the two
answers held and released in the reverse order. `FEN-02` to `FEN-06` come
from the plan's browser sweep of the editors' fields on 11 November; the
main session re-ran each in a browser on a fresh project. `FEN-07` comes
from the plan's phase 5, a reader from a second model family (Codex) over
the code changed since the 31 October audit, and was reproduced in a
browser on 13 November. `FEN-08` to `FEN-10` come from the same reader;
a worker ran each in a browser, and the main session ran each journey
again on 13 November.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| FEN-01 | Planned | P3 | The Databricks schema list is the chosen catalog's, whichever answer arrives last. |
| FEN-02 | Planned | P2 | A Filter's "is one of" list starts at its column's type and takes pasted cells as separate values. |
| FEN-03 | Planned | P3 | A Rename to a name the frame already has is refused at the step, and a step the engine refuses shows the engine's sentence without its plan or a file path. |
| FEN-04 | Planned | P3 | A Constant value whose name is cleared is kept, or the save says so; the panel shows what the file holds. |
| FEN-05 | Planned | P3 | An edit to a Banding cell is one undo step. |
| FEN-06 | Planned | P3 | A Banding factor whose input column is gone is marked when the editor opens and counted among its Issues. |
| FEN-07 | Planned | P2 | A Group by with a row count, saved and opened again, is a step the analyst can edit, not an "Invalid step" whose only action is Delete. |
| FEN-08 | Planned | P3 | The Output editor's preview follows an Undo of a change upstream. |
| FEN-09 | Planned | P3 | A Banding factor switched to its other type gets its own remembered rules, never another factor's after an Undo. |
| FEN-10 | Planned | P3 | Values imported into a Banding factor are dropped when the factor was removed before they arrived. |

## Planned improvements

### FEN-01 — A late answer does not replace the chosen catalog's schemas
**Why:** Reproduced on 10 November 2026 in a browser. In a Databricks Data
Input, the analyst picks one catalog and then another before the first list
of schemas has arrived. When the first catalog's answer comes last, it
replaces the list: the Catalog box shows the second catalog and the Schema
box offers no schema at all, with nothing said, as if the catalog were
empty. Clicking into the Schema box asks again and recovers. The request has
no guard, while the table picker in the same file keeps "only the answer to
the latest ask". A failed older request would show its error against the
newer catalog by the same path (read, not run). The overlap needs only that
the first list is slower than the second.

**Plan:** Give the catalog and schema requests the guard the table picker
has: an answer for anything but the current choice is dropped.

**Acceptance:** With the answers for two catalogs delivered in either order,
the Schema box lists the chosen catalog's schemas, and an older failure
shows no error against the newer choice.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/_DatabricksSelector.tsx`.

### FEN-02 — An "is one of" list takes its column's type and pasted cells
**Why:** Reproduced on 11 November 2026 in a browser by the plan's sweep of
the editors' fields, and again by the main session. In a stepped Transform,
a Filter on `proposer_age`, a whole-number column, is changed from "equals"
to "is one of" and 24 is added to the list. The list's type starts at "text"
whatever the column is, so the step becomes `is_in(["24"])`. The preview
shows the engine's words, "'is_in' cannot check for Int64 values in
List(String) data", followed by its query plan and the data file's full
path. Save writes the step, so a run fails the same way. The field already
knows the column's type: it uses it to choose between whole and decimal
numbers.

The same box takes pasted cells as one value. Two cells copied from a
spreadsheet row, `direct` and `broker`, become one value holding a tab. The
preview shows no rows and no message, and the file holds
`is_in(["direct\tbroker"])`. The sweep saw a pasted column lose its line
breaks and become `directbroker`. The Rating grid splits pasted cells.

**Plan:** Start an empty list at the type of the column it is compared
with, and refuse a value of another type at the field in words. Split a
paste at tabs and line breaks into one value each.

**Acceptance:** "is one of" on a whole-number column adds 24 as a number,
and the preview filters on it. Two cells pasted from a spreadsheet row, and
three from a column, become that many values. A browser test does both.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/fields.tsx`;
`src/haute/_polars_steps.py::_members`.

### FEN-03 — A Rename to a name the frame already has is refused at the step
**Why:** Reproduced on 11 November 2026 in a browser by the sweep, and
again by the main session. In a stepped Transform's Rename step, `channel`
is renamed to `value`, a column the frame already has. Nothing is said at
the field. The preview shows the engine's words, "column 'value' is
duplicate", with its query plan and the data file's full path. Save writes
`df.rename({"channel": "value"})` and a run fails the same way. The step's
form has the frame's columns: its picker for the column to rename suggests
them.

**Plan:** Check the new name against the frame's other columns when it is
committed, and refuse it at the field naming the column that already has
it. Keep the engine's first sentence in the preview's message and leave its
plan and the file's path to the log.

**Acceptance:** Renaming a column to an existing column's name is refused
at the field in words, and the file keeps the step as it was. A step that
still fails in the engine shows the engine's first sentence without the
plan or a path.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/polarsSteps/forms.tsx`;
`src/haute/_polars_steps.py::step_refusal`.

### FEN-04 — A Constant whose name is cleared is not dropped in silence
**Why:** Reproduced on 11 November 2026 in a browser by the sweep, and
again by the main session. In a Constant node the analyst clears a value's
name to retype it, and Save runs before the new name is typed. Save answers
200 with no message. The settings file has lost the value: `base_rate` and
its 100 are gone and only `brand` remains. The panel still shows the row,
an empty name beside 100, until a reload removes it. The server drops the
row by design, "A row with no name, which a run skips, is not written", and
the name field refuses only a duplicate.

**Plan:** Refuse an empty name at the field, as a duplicate is refused, so
the row keeps its last name until a new one is typed. Failing that, say at
the save that a value has no name. Either way the panel shows what the file
holds once the save has answered.

**Acceptance:** Clearing a value's name and saving leaves the value in the
file under its last name, or the save says that a value has no name. After
the save the panel shows what the file holds.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/ConstantEditor.tsx`;
`src/haute/_constant_config.py`.

### FEN-05 — A Banding cell's edit is one undo step
**Why:** Reproduced on 11 November 2026 in a browser by the sweep, and
again by the main session. In a Banding node the analyst retypes a
breakpoint's band name, `Age 40 or below`, as `Young`, presses Undo once
and saves. The field reads `Youn` and the file holds `"label": "Youn"`:
each keystroke is its own undo step. The breakpoint grid's boundary and
band name are plain inputs that write on every keystroke, and the
categorical rules grid is written the same way (read, not run). The
editors' shared text field exists to prevent this; its own comment says
"typing an N-character value recorded N undo entries". The sweep saw the
Rating Step's Default, a Limit's rows and an Edge Join's suffix each undo
in one step.

**Plan:** Give the two Banding grids the committed field, or its buffering,
so that a cell's edit reaches the graph once, when the cell is left or
Enter is pressed.

**Acceptance:** A band name retyped and undone once returns to the earlier
name, in the field and in the file after a save. The same holds for a
boundary and for a categorical rule's value and group. A browser test types
and undoes.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/banding/BreakpointGrid.tsx`;
`frontend/src/panels/editors/banding/BandingRulesGrid.tsx`;
`frontend/src/components/form/CommittedTextField.tsx`.

### FEN-06 — A Banding factor whose input column is gone is marked at once
**Why:** Reproduced on 11 November 2026 in a browser by the sweep, and
again by the main session. An upstream column that a Banding factor reads
is renamed, so the factor's settings name a column the input no longer has
(`proposer_agee` here). On opening the node, Input Column is a plain text
box holding the name, with no mark. After a Refresh it becomes a list
reading "proposer_agee (not in input)", but the panel's Issues filter still
answers "No matching columns". The sweep's journeys saw the factor's dot
stay green, and saw the Edge Join and the Rating Step editors mark such a
value as soon as they open. A run refuses the column by name, so nothing
wrong is priced; the editor is where the analyst looks first.

**Plan:** Ask for the input's columns when the editor opens, mark a saved
column that the input does not have as soon as they are known, and count
the factor among the Issues.

**Acceptance:** Opening a Banding node whose factor names a missing column
shows the column as not in the input without a Refresh, and the Issues
filter lists the factor.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/BandingEditor.tsx`.

### FEN-07 — A saved Group by with a row count opens as a step
**Why:** Reproduced on 13 November 2026 in a browser. A Transform's saved
code is what the steps editor writes for a Group by on `channel` with a row
count named `policy_count`:
`df.group_by("channel", maintain_order=True).agg(pl.len().alias("policy_count"))`.
Opened again, its panel shows a card titled "Invalid step" that reads "The
group and aggregate step aggregation 1 is missing "column" or "name"." and
"This step cannot be edited here. Delete it to repair the step list." The
card does not open; its one action is Delete. The generated code beneath it
is unchanged and runs, so an analyst who does as the card says deletes a
working aggregation.

The server rebuilds the aggregation with a name and no `column`,
`{"agg": "len", "name": "policy_count"}`, as the syntax specification
requires: "A group-by row count (`len`) has no `column`." The browser's
check of a step demands a text `column` in every aggregation that has no
column type. Built in the panel, the entry still carries the empty `column`
it started with and passes, so the fault appears only after a reload. The
form and the summary beside the check already treat a row count as having
no column.

**Plan:** Let the step check accept a row count without a column, by the
same list of column-less aggregations the form uses.

**Acceptance:** The node above opens as a Group by card that can be opened
and edited. A test gives the browser's step check every step list the
server rebuilds from the code the server renders, for each aggregation and
each window function, and none is called invalid.

**Dependencies:** None.

**Evidence:**
`frontend/src/panels/editors/polarsSteps/catalogue.ts`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx`;
`frontend/src/panels/editors/polarsSteps/forms.tsx`;
`src/haute/_polars_steps_lift.py::_Reader`;
`specs/codegen/structured-syntax-boundary.md`.

### FEN-08 — The Output editor's preview follows an Undo upstream
**Why:** Reproduced on 13 November 2026 in a browser. The Quote Response's
editor is open and its Output preview shows `value_doubled` as 22. The
Transform upstream is changed from `* 2` to `* 3`, and the preview, opened
again, shows 33. Undo in the toolbar puts `* 2` back. The Output preview
still shows 33, with no note. Closing the Output preview and opening it
again sends no request and shows 33; only Refresh shows 22.

The preview is kept under a key made of the output mapping and the source.
The nodes upstream, whose code decides the values, are not in it. Going to
another node and back makes the editor ask again, so the old answer needs
the editor to stay open through the Undo.

**Plan:** Put what the preview was computed from into its key: the graph's
structural version, as the node previews use.

**Acceptance:** In the run above the Output preview shows 22 after the
Undo, or says it is out of date, without Refresh.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/OutputEditor.tsx`;
`src/haute/routes/output_assemble.py`.

### FEN-09 — A Banding factor's remembered rules stay with it through an Undo
**Why:** Reproduced on 13 November 2026 in a browser. A Banding has a
Numeric factor `proposer_age` and then a Categorical factor `vehicle_age`
with the rules 0 to "New" and 1 to "Nearly new". `vehicle_age` is moved
first and switched to Numeric, and two Undos put the order and the type
back. `proposer_age` is then switched to Categorical. It is given
`vehicle_age`'s rules, 0 to "New" and 1 to "Nearly new", and Save writes
them into its settings. Without the move and the Undos its rules are
empty.

The editor remembers the rules a factor had under its other type, so a
switch back does not lose them, and it remembers them by the factor's
position. Moving a factor forgets what was remembered; undoing the move
does not, so the rules sit at a position another factor now holds.

**Plan:** Remember the rules by the factor, not by its position, or forget
them whenever the list of factors is replaced, as on an Undo.

**Acceptance:** After the steps above `proposer_age` has no rules as a
Categorical factor, and `vehicle_age` still has its two.

**Dependencies:** `FEN-05` changes what one Undo covers in the same editor.

**Evidence:** `frontend/src/panels/editors/BandingEditor.tsx`.

### FEN-10 — Imported values that arrive after their factor was removed are dropped
**Why:** Reproduced on 13 November 2026 in a browser. A Banding has two
Categorical factors, `channel` and `vehicle_age`. An import of values into
`channel` from a column of a params file is started, and the read of the
column is slow (the run held it). `channel` is removed. When the read
answers, its values, North, South and East, are added to `vehicle_age`'s
rules, and Save keeps them. Nothing is said. Choosing another factor
while an import is pending cancels it; removing the factor does not,
because the import's dialog is kept by the factor's position, which the
next factor now has.

**Plan:** Tie a pending import to its factor, and drop its answer when
that factor is gone.

**Acceptance:** In the run above `vehicle_age` keeps only its own rule
after the read answers, and the removed factor's import adds nothing.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/BandingEditor.tsx`;
`frontend/src/panels/editors/banding/ImportParamValues.tsx`.
