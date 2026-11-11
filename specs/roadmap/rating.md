# Rating roadmap

## Scope

Banding and Rating Step, as [the rating specification](../rating/high-level.md)
describes them: how a rule's or an entry's value is matched to a column's
values, what a mismatch tells the analyst, and what a typed number becomes.
`RTG-01` and `RTG-02` come from the review plan's sweep of 10 November
2026, which ran every node type on a fixed set of awkward values, and were
reproduced on that day through the node's preview. `RTG-03` and `RTG-04`
come from the plan's browser sweep of the editors' fields on 11 November;
the main session re-ran them in a browser and from the command line.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| RTG-01 | Planned | P2 | A categorical Banding rule written `true`, `True` or `1` for a Boolean column matches it or is refused; it no longer sends every row to the default in silence. |
| RTG-02 | Planned | P3 | A Rating Step entry whose key is not a value of its factor's type is refused naming the table, the factor and the key. |
| RTG-03 | Planned | P2 | A decimal comma typed into a number box of the editor (a Rating Step's Default, a Banding breakpoint, a model's validation share, an optimiser's bounds and tolerance) is kept or refused in words, the same in every browser, never saved ten times too large. |
| RTG-04 | Planned | P3 | A Rating table with a Default and no entries prices the Default, instead of stopping the run for a column it did not create. |

## Planned improvements

### RTG-01 — A Banding rule that can never match its column is not silent
**Why:** Reproduced. A categorical Banding keys each rule by Python's text
for its value and matches it against the column's text as Polars writes it.
For a Boolean column the two differ: a rule written `true` in the settings
file becomes the key `True`, and the column reads `true`. With rules for
`true` and `false` on a Boolean column, every row took the default, the
status was ok and nothing was said. The text `"true"` matched; `"True"` and
`1` did not, again in silence. A Rating Step keys its entries through the
column's own type, which the specification calls one dtype-faithful key
form shared everywhere, and it matched `true`, `"true"` and `1` alike. In
the sweep, Banding rules keyed by integers, decimals, dates and text all
matched; a Boolean is the type where the two forms part. A settings file
written by hand holds `true`, not `"true"`.

**Plan:** Key a categorical rule through the form a Rating Step entry
takes, by the column's type. Refuse a rule whose value is not a value of
that type, naming the factor and the rule, as an unreadable breakpoint is
refused.

**Acceptance:** On a Boolean column, rules written `true` and `false`,
`"true"` and `"false"`, and `1` and `0` each band the rows as the same keys
rate them in a Rating Step. A rule written `"maybe"` on a Boolean column,
or `"abc"` on an integer column, is refused naming the output column and
the rule's value. A test runs one table of column types and spellings
through both nodes and expects the same matches.

**Dependencies:** None.

**Evidence:** `src/haute/_rating.py::banding_categorical_claims` keys a rule
by `str(value)`; `src/haute/_rating.py::normalise_rating_key` is the form to
share; `specs/rating/high-level.md`.

### RTG-02 — A key of the wrong type is refused by table, factor and key
**Why:** Reproduced. A Rating Step entry keyed by text on a factor column
of another type fails in Polars' own words. `"NE"` on an integer column:
"conversion from `str` to `i64` failed in column 'area' for 2 out of 2
values", followed by the expression `col("area").strict_cast(Int64)`. On a
Boolean column, `"True"` included: "incomplete mapping specified for
`replace_strict`. Hint: Pass a `default` value to set unmapped values." On
a datetime column: "could not find an appropriate format to parse dates,
please define a format". None names the table or says that the entry's key
is not a value the column can hold, and the Boolean hint points at a
setting the node does not have. A Banding's refusal of the same kind of
mistake is nearly right: "Banding output 'period' has date breakpoints, but
its column is Int64" names the output and the type and leaves out the
column, which the specification says it names.

**Plan:** Check a table's entry keys against each factor column's type
before the lookup is built. Refuse naming the table's output column, the
factor, the first keys that do not fit and the type the column has. Add
the column's name to the Banding refusal.

**Acceptance:** Each of the three cases above is refused in a message that
names the table, the factor column, a key and the column's type, and holds
no Polars expression or hint. The Banding refusal names the input column as
well as the output.

**Dependencies:** None.

**Evidence:** `src/haute/_rating.py::_rating_key_expr`;
`src/haute/_rating.py::banding_comparison_expr` words the Banding refusal;
`specs/rating/high-level.md`.

### RTG-03 — A decimal comma is not dropped from a relativity or a boundary
**Why:** Reproduced on 11 November 2026 in a browser by the plan's sweep of
the editors' fields, and again by the main session. In a browser set to
English, an analyst who writes decimals with a comma types `1,5` into a
Rating Step table's Default. The box shows `15`, with no message, and Save
writes `"default_value": "15"`. The saved files were then run from the
command line with one entry in the table: the rows that entry matched were
priced at 1.2 and every other row at 15.0. A Banding breakpoint typed `40,5`
is saved as 405 the same way. In a browser set to German the same keystrokes
give 1.5 and 40.5, so the result depends on the browser's language and not
on the project. Both boxes are the browser's own number fields, which
discard a character their language does not use. `WB-48` records the same
fault on the workbench's sheet.

The same kind of field serves the rest of the editor: 23 boxes in 12 files
(read). More were typed into on the same day, `0,5` each time, in a
browser set to English, and each was saved with no message. A model's
validation share became 5 per cent. An optimiser's swept bound became 5
where 7.5 stood, and its tolerance 5 where 0.0001 stood.

Two smaller things from the same runs. The Default is written as text,
`"15"`, where the file held the number 1; the sweep saw an edit to any cell
of the table do the same to an untouched Default. And a relativity cell
typed `1,2` is marked with a hover title only, while Save reports success
and the cell still shows `1,2`; a reload shows the earlier value (seen by
the sweep, not re-run).

**Plan:** Take every number box of the editor through one text field that
reads a number by a stated rule, the one `WB-48` plans for the sheet: a
comma is refused in words at the field, or read as the decimal mark, the
same in every browser. Write the Default as a number.

**Acceptance:** In a browser set to English and in one set to German, `1,5`
typed into a Rating Step's Default and `40,5` into a Banding breakpoint are
either kept as 1.5 and 40.5 or refused at the field in words. Neither is
saved as 15 or 405, and the settings file holds the Default as a number.
The same holds for a model's validation share and an optimiser's bounds
and tolerance. A browser test types the comma.

**Dependencies:** `WB-48` for the rule.

**Evidence:** `frontend/src/panels/editors/RatingStepEditor.tsx`;
`frontend/src/panels/editors/banding/BreakpointGrid.tsx`;
`frontend/src/components/form/CommittedTextField.tsx`;
`frontend/src/panels/modelling/NumberField.tsx`;
`frontend/src/panels/modelling/SplitAndMetricsConfig.tsx`;
`frontend/src/panels/optimiser/OptimiserConstraintSettings.tsx`;
`frontend/src/panels/OptimiserConfig.tsx`;
`src/haute/_rating.py::_apply_rating_table`.

### RTG-04 — A Rating table with a Default and no entries prices the Default
**Why:** Reproduced on 11 November 2026 from the command line, on the
project the repository's own browser tests scaffold. Its Rating Step has
three factors, an output column and a Default of 1.0, and no entries yet.
`haute run` stops at it: "'browser_rating' did not create the column
'browser_relativity', which its contract says it outputs." It was the same
with the Default set to 15. With one entry typed, the node runs and every
row without an entry takes the Default.

Two parts of the engine disagree about the empty table. The rating code
treats it as a documented no-op and logs that the table was skipped for "no
entries"; the node's contract then refuses the missing column. Neither
tells the analyst that the table needs an entry. The editor says of a
Default, "The default fills every miss"; with no entries every row is a
miss, and none is filled.

**Plan:** Let a table with factors, an output column and a usable Default
produce its column from the Default when it has no entries. Keep the skip
for a table that has neither, and have its refusal say that the table has no
entries and no Default.

**Acceptance:** A Rating Step whose table has a Default and no entries runs,
in the preview and from the command line, and every row holds the Default. A
table with no entries and no Default is refused naming the table and saying
what it lacks.

**Dependencies:** None.

**Evidence:** `src/haute/_rating.py::_rating_table_skip_reason`;
`src/haute/_rating.py::_apply_rating_table`;
`frontend/src/panels/editors/RatingStepEditor.tsx`;
`scripts/run_frontend_e2e_server.py`.
