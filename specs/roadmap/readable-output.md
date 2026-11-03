# Readable output roadmap

## Scope

These packages cover what Haute writes into a project's repository, read as a diff:
- `main.py` and its `.haute.json` sidecar;
- the node configs under `config/` and the global constants;
- the Workbench form folder (`workbench/`) and the test quotes;
- the files `haute init` scaffolds.

The aim is that a reviewer who never opens the editor can follow a pull request, and that an analyst can develop in code. One change in the editor should make a small diff that says what changed, and a hand edit to a file should survive the next load and save.

The packages come from an investigation of [commercial-pricing pull request #1](https://github.com/PricingFrontier/commercial-pricing/pull/1) on 3 November 2026. It found:
- **Polars nodes are stored twice.**
  - The body in `main.py` is what runs, and an expression tree in `config/polars/<node>.json` holds the steps again.
  - The steps are kept only while their rendering is the same syntax tree as the body, with the same comments.
  - Adding one term to a 12-term sum changes 1 line of Python and 39 lines of JSON.
- **Six JSON writers follow different rules.** A prototype printer rewrote the pull request's 41 JSON files with no change to what they hold: under a 100-column limit they went from 2,990 lines to 1,652. `READ-10` measures again with its own printer.
- **A rating table takes 5 lines per cell.**
- **Inserting a node into `main.py` moves unrelated functions.**
- **Random ids fill the Workbench form folder**, such as `c_818cc52f3c8b78a9`, `t_d7d9abf03a7cf621` and `collection_nj2i`. The built-in `quote_details` columns are the exception: their ids are fixed names such as `qd_insured`.

The owner decided the following on 3 November 2026:
- **Polars code lives only in `main.py`.** Haute rebuilds as many steps as it can from the code. Statements it cannot recognise become a code step, and the rest of the node stays as steps.
- **Only commercial-pricing is converted to each new format.** No other project needs converting (`READ-20`).
- **Workbench columns and tables have no ids.** Their names identify them. Following a rename in the quote store's SQL is left to a later package (`READ-33`).
- **Sheets, sections and components have no random ids.** A sheet is identified by its file name, a section by its title, and a component by a number (`READ-24`).
- **`main.py` keeps its function order.** A new node's function goes just above the first function that uses it.
- **Formats:**
  - rating tables are written as a grid;
  - constants are a name-to-value map, and constants that only formulas read become global constants;
  - schema keys on disk are snake_case;
  - generated workflows repeat nothing.

**Out of scope:** `haute init` rewrites `haute.toml` from scratch on a re-run, as decided on 1 November 2026, so editing that file in place is not planned.

The packages fall into two groups:
- **Writing changes:** `READ-10` to `READ-13` change how files are written, but every file loads to the same values. The next save rewrites each file once, and no conversion is needed.
- **Format changes:** `READ-20` onwards. The loader accepts only the current format ([specification governance](../README.md)), and `READ-20` says how commercial-pricing moves to each new one.

Each package states its formats in its owning specification before behaviour changes, and names the contract it replaces there. Golden tests are written from those specifications, not from the implementation's output.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| READ-10 | Planned | P2 | One printer writes every committed JSON file, with a fixed shape per kind of record. |
| READ-11 | Planned | P2 | Node positions are whole numbers, on one line per node, and only a move changes them. |
| READ-12 | Planned | P2 | Adding or removing a node leaves every unaffected function in `main.py` where it was. |
| READ-13 | Planned | P3 | Generated code leaves out a default it repeats and a comment that says nothing, and new nodes get plain names. |
| READ-20 | Planned | P2 | Each format change comes with a one-off script that converts commercial-pricing and Haute's fixtures, proven before and after. |
| READ-21 | Planned | P2 | A prototype proves that Haute can rebuild every kind of Polars step from the code it renders. |
| READ-22 | Planned | P2 | A Polars node is stored only as code in `main.py`, and a hand edit or comment keeps the rest of its steps. |
| READ-23 | Planned | P2 | Workbench columns and tables are identified by their names, with no ids in the form folder or a release. |
| READ-24 | Planned | P2 | A sheet, its sections and their components are identified by file name, title and number, not random ids. |
| READ-25 | Planned | P2 | A column's sourced allowed values are stored only as a reference to their parameter file. |
| READ-26 | Planned | P2 | A rating table is written as a grid, one row of the table per line. |
| READ-27 | Planned | P3 | Banding is written as a list of bands, each with its upper edge and its label. |
| READ-28 | Planned | P3 | Constants are a map from name to value, and constants that only formulas read become global constants. |
| READ-29 | Planned | P3 | Every key Haute's schemas own is snake_case on disk; keys the analyst names are untouched. |
| READ-30 | Planned | P3 | `main.py` holds a `connect()` call only for an edge the signatures cannot express. |
| READ-31 | Planned | P3 | Gaps, conditions and appearance on a sheet are written where a reader looks for them. |
| READ-33 | Deferred | P2 | A column or table renamed against the project's default branch keeps its saved values in the quote store. |
| READ-40 | Planned | P3 | Generated workflows declare each secret, setup and check once. |
| READ-41 | Planned | P3 | A new project holds no `uv init` placeholders and no settings at their default values. |

## Planned improvements

### READ-10 — One printer writes every committed JSON file
**Why:** Six writers follow four key-order rules and two rules for defaults:
- node configs sort only their top-level keys and write empties and nulls;
- global constants have a writer of their own;
- the sidecar writes positions as 17-digit floats;
- the Workbench folder puts only all-number objects on one line;
- test quotes use plain `json.dumps`.

Two more problems:
- A rating table cell takes 5 lines.
- Nested keys drift: the editor writes `defaultValue` after `entries` and moves it back on the next edit, making a diff that changes nothing.

**Plan:**
- Write every committed JSON file with one printer and one atomic writer: UTF-8 with nothing escaped, LF, two-space indent and a final newline.
- Shape records by kind, not by width. Under a width limit, a one-character edit can spread a whole record over many lines. One line each for:
  - a rating entry;
  - a node position;
  - a layout box;
  - a schema column;
  - a show-when condition.
- Keep long field lists vertical.
- The owning specifications list, for each kind of record, its key order (identifying key first) and its one-line or vertical shape. Maps whose order means nothing are sorted, and the analyst's data keeps the order it was written in.
- Leave out values equal to their defaults, by each schema's own rules (an empty `combinedOutputs` among them), not by a generic "drop empties" pass.
- Samples keep values as typed, text included, so that a draft that doesn't validate can still be saved.
- Keep the release form's serialisation (`render_form`) separate: it is what a release ships, not a source file.

**Acceptance:**
- A golden test for each kind of file, written from its specification.
- Saving twice gives identical bytes.
- Rewriting the reference pipeline and the workbench fixture changes only whitespace, key order and values left out at their defaults. Every file loads to the same values.
- The line count of the investigated pull request's files, measured again with this printer.

**Dependencies:** None. Land it on `main` before other branches move on, since the next save rewrites each file once.

**Evidence:** `src/haute/_config_io.py::node_config_json`;
`src/haute/_config_io.py::global_constants_json`; `src/haute/routes/_helpers.py::save_sidecar`;
`src/haute/_workbench_folder.py::_dumps`; `src/haute/_workbench_form.py::render_form`;
`src/haute/_workbench_test_quotes.py::test_quote_bytes`.

### READ-11 — Node positions are whole numbers
**Why:**
- The sidecar holds positions such as `-355.61225658228415` beside `2332.0`, over 4 lines per node.
- In one commit of the investigated pull request, a one-word Polars edit moved five nodes by 1 to 3 pixels, and nothing explains why.

**Plan:**
- Round positions to whole pixels in the canvas when a node is placed or dragged, as well as on save. The editor compares positions exactly, so rounding only on save would mark every node as moved.
- Find the cause of the 1 to 3 pixel moves, and stop an edit that moves no node from writing positions.
- One line per node comes from `READ-10`.

**Acceptance:**
- A store test: a drag ending at fractional coordinates stores whole numbers.
- Editing a Polars step and saving leaves every position unchanged.
- Loading and saving an unchanged pipeline leaves its sidecar byte for byte the same.

**Dependencies:** `READ-10` for the one-line shape.

**Evidence:** `frontend/src/stores/useGraphStore.ts::positionsEqual`;
`src/haute/routes/_helpers.py::save_sidecar`.

### READ-12 — Adding or removing a node leaves unaffected functions where they were
**Why:** Functions are written in a topological order with each data source placed just before its first consumer. Inserting a node can push everything below it further down and reshuffle functions that did not change. One commit in the investigated pull request moved two unchanged functions. The `br_` and `ce_` chains are also interleaved.

**Plan:**
- Keep the function order the file has.
- A new node's function goes just above the first function that uses its output, or at the end when nothing does yet. Several new nodes go in the order they were created, each by the same rule.
- A removed node's function goes, and nothing else moves.
- Submodel files follow the same rule.
- A function that consumes a new or renamed node changes its signature, but stays where it is.
- Replace the topological-order contract in the [codegen specification](../codegen/high-level.md).
- A one-off regrouping of existing files is not planned.

**Acceptance:** Codegen tests on the reference pipeline and a submodel:
- inserting several nodes, removing a node and renaming a node leave every unaffected function byte for byte the same and in the same relative order;
- an unchanged pipeline regenerates byte for byte.

**Dependencies:** None.

**Evidence:** `src/haute/codegen.py::_emission_order`; `src/haute/_topo.py::_topo_sort_known_ids`.

### READ-13 — Generated code leaves out what it repeats
**Why:**
- Every join carries `suffix="_right"`, though it is the default.
- The `# Wire nodes together - edges define data flow` comment says nothing a reader needs.
- A new node's name is its type plus a counter for the session, so a pipeline's only Workbench Output is called `Workbench_Output_4`.

**Plan:**
- Drop `suffix` when it is the default.
- Keep `how`, because Haute's default (`left`) differs from Polars' (`inner`).
- Keep `maintain_order`, which the determinism rule requires.
- Drop the wire comment.
- Name a new node by its type in snake_case (`workbench_output`), numbered only when the name is taken (`polars_2`). Existing names don't change.

**Acceptance:**
- A codegen golden without the default suffix or the comment.
- Frontend tests:
  - a pipeline's first Workbench Output is named `workbench_output`;
  - a second node of a type takes the next free number;
  - names stay as they are after a reload.

**Dependencies:** None.

**Evidence:** `src/haute/_source_layout.py::WIRE_COMMENT`;
`frontend/src/utils/flowElements.ts::nodeLabel`.

### READ-20 — Each format change converts commercial-pricing with a one-off script
**Why:** The loader accepts only the current format. The canonical-only rule in the [specification governance](../README.md) forbids any production code that converts an old format: "Nothing migrates it". Each format change from `READ-22` onwards must still bring commercial-pricing across. The owner decided on 3 November 2026 that no other project needs converting, so the rule stays and Haute gets no `haute migrate` command.

**Plan:**
- Each format change's pull request carries a one-off conversion script. The script:
  - converts Haute's own fixtures in that pull request;
  - is run by the owner once on commercial-pricing, in a commit of its own;
  - is not kept in Haute's code or tests once the pull request merges.
- **File by file:** it converts each file still in the old format and leaves alone each file already in the new one. So it can run on a project in which some files are already converted. It refuses a file in neither format, naming it, and changes nothing.
- **A commercial-pricing branch cut before a conversion** runs the script on itself, commits, then merges `main`. If the merge comes first, running the script afterwards converts the files the branch added in the old format.
- A comparison against a revision older than a conversion, such as the schema pane's comparison with the default branch, is refused with a message naming the conversion. Today it would hand old bytes to the current parser.
- Any new file a conversion creates is in the deploy bundle.

**Acceptance:**
- Each conversion is proven on the reference pipeline, the workbench fixture and commercial-pricing:
  - the converted files load to the same values;
  - saving twice gives identical bytes;
  - against results recorded before the conversion, the sample prices the same, and every node's output columns and types match.
- A branch that added and edited old-format files keeps both edits after it is converted and merged, in either order.

**Dependencies:** None.

**Evidence:** `specs/README.md`; `src/haute/_workbench_folder.py::form_at_ref`.

### READ-21 — Every Polars step kind is rebuilt from the code it renders
**Why:** Storing a Polars node only in `main.py` (`READ-22`) depends on Haute rebuilding steps from code: about 1,000 lines that reverse the step renderer. Some forms are ambiguous:
- a pivot and a filtered group-by render to the same code;
- two code steps side by side merge;
- the `divide` pattern has to be recognised.

The rebuilt steps also carry nothing the code doesn't hold, such as step ids or a formula's typed text.

The [syntax-boundary specification](../codegen/structured-syntax-boundary.md) does not rule this out. It rejects a graph model taking authority over arbitrary Python, not rebuilding the steps Haute supports from the code it writes.

**Plan:**
- Before anything changes on disk, build the step rebuilder as a prototype.
- Define the canonical form of a step list:
  - code steps next to each other are merged into one, since the rendered code holds no boundary between them;
  - so a run of statements Haute cannot recognise is one code step, however many statements it holds;
  - each ambiguous form becomes one documented step;
  - ids and display-only text are left out.
- Prove two properties over every step kind in the catalogue, alone and in chains:
  - rebuilding rendered steps gives their canonical form;
  - rendering the rebuilt steps gives the same code.
- Cover compound statements, comments, and code mixing recognised and unrecognised statements.
- A form Haute renders but cannot rebuild is a defect, unless that step kind changes how it renders.

**Acceptance:**
- A property test over the step catalogue passes both properties.
- The specification records the canonical form and each ambiguous form with its chosen step.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_steps.py::_Renderer`; `src/haute/_polars_steps.py::render_node_steps`;
`src/haute/_config_builder.py::_reconcile_steps`.

### READ-22 — A Polars node is stored only as code in `main.py`
**Why:** Today the code in `main.py` runs, and `config/polars/<node>.json` holds the steps again as an expression tree. The steps are kept only while their rendering is the same syntax tree as the code, with the same comments. That causes several problems:
- the tree nests one level per term in a chain of one operator, so such a chain is capped at 13 terms (`MAX_EXPR_DEPTH = 12`);
- adding a term changes 39 lines of JSON;
- editing the JSON by hand discards the steps;
- editing the code by hand, or adding a comment to it, turns the whole node into code.

**Plan:**
- Drop `config/polars/*.json` and the `config=` argument on `@pipeline.polars`.
- **Rebuilding steps on load:**
  - Haute rebuilds the steps from the node's code (`READ-21`).
  - A run of statements it cannot recognise becomes one code step, and the statements around it stay steps.
- **Column settings** such as `selected_columns` and `column_renames` go into the decorator's keywords, as a code-only Polars node writes them today.
- **Unfinished steps:**
  - While any step of a node is unfinished, the node is saved as an incomplete transform is today:
    - its whole step list goes in the `.haute.json` sidecar under the node, the steps before and after the unfinished one included;
    - its code is today's placeholder, which raises;
    - the save warns.
  - So the node fails in the editor, `haute run`, a standalone run and a deploy alike, and never returns the steps before the unfinished one.
  - Once every step is complete, the steps move into code and leave the sidecar.
- **Comments:**
  - a comment belongs to the statement below it, and moves or goes with that step;
  - a comment at the end of the body stays at the end;
  - comments never turn a node into code.
- **Chains:**
  - a chain of one operator becomes one step with a list of terms, so the 13-term cap goes;
  - grouping and the order of evaluation are kept: floats are never reassociated, and `sum_horizontal` is never substituted, since it treats missing values differently.
- **Step ids** come from each step's kind and position on load. The assistant's step edits keep their stale-revision protection, so ids from an older load are refused.
- **The formula box** shows each formula from the rebuilt step, so Haute needs no second implementation of the formula language.
- **Converting a project:**
  - the JSON is deleted once the code rebuilds to the same steps;
  - a node that doesn't is reported, its code stays, and its unrecognised statements become code steps;
  - a node with an unfinished step moves its step list into the sidecar.
- Replace the stepped-transform section of the [pipeline-config specification](../pipeline-config/high-level.md).

**Acceptance:**
- Loading and saving a node's code is byte-stable.
- A hand edit to one statement keeps the other steps, and a comment keeps the steps.
- A 20-term sum is one step.
- An unfinished middle step, with complete steps before and after it, round-trips. The node fails in the editor, `haute run`, a standalone run and a dry-run deploy.
- The column settings round-trip.
- Two unrecognised statements next to each other load as one code step.
- Tests for grouped arithmetic and for arithmetic with missing values give the same results as today.
- The formula box round-trips each formula.
- The reference pipeline gives every node the same output columns, types and sample values before and after its conversion.

**Dependencies:** `READ-20`, `READ-21`.

**Evidence:** `src/haute/_config_builder.py::_same_program`; `src/haute/_polars_steps.py::MAX_EXPR_DEPTH`;
`src/haute/_codegen_builders.py`; `frontend/src/panels/editors/polarsSteps/formula.ts::additive`;
`src/haute/assistant/_catalog.py`.

### READ-23 — Workbench columns and tables are identified by their names
**Why:** Every column in `schema/*.json` carries a random id (`"id": "c_818cc52f3c8b78a9"`, in two widths), and every table carries one too (`t_d7d9abf03a7cf621`). The sheets already refer to fields by name (`br_jobsites.limit`). The ids have these uses:
- a comparison against the default branch treats a rename as a rename;
- they key cells in memory;
- they mark `quote_details`' store-only columns;
- they go into the names Haute gives the store's constraints, indexes and the temporary names of a two-step rename;
- they are part of the release form, whose bytes make the release's id.

The owner judged that the complexity isn't worth it.

**Plan:**
- **The rule:** a table's name, whose lower-case form names its file, identifies it. Table names are unique ignoring case. A column's name identifies it within its table, and is also unique ignoring case.
- **What changes:**
  - remove the column and table ids from the form, the form folder, the release form and the editor;
  - key cells, comparisons, `quote_details`' store-only columns and the editor's selection by name;
  - derive constraint, index and temporary names from names;
  - no id the editor keeps in memory reaches a file or a release, so building one form twice gives one release id.
- **Existing stores keep their constraint and index names.** Haute names them only when it creates a table, and nothing looks them up by name afterwards. The implementation confirms this for every schema change and for the app's check.
- **Renames:**
  - a renamed column reads as a column removed and a column added, and a renamed table as a table removed and a table added;
  - the store keeps the removed column, and quotes keep their values there through saves (the `$kept` field, [workbench](../workbench/high-level.md));
  - the renamed column starts empty;
  - the schema pane already pairs a renamed column with its column in the database, when comparing against a database;
  - `READ-33` owns pairing against the default branch.
- **One-off and contract changes:**
  - The release id changes once, because the release form's bytes change.
  - Replace the stable-id rationale in the [workbench specification](../workbench/high-level.md).
  - `READ-20` removes the ids from existing forms.

**Acceptance:**
- No id remains in the form model, the folder files or a release form.
- Building a form twice gives the same release id.
- The store comparison, the cell reader and the store-only columns work by name.
- A renamed column shows as one column removed and one added, and the old column keeps its values through a save.
- Table or column names that differ only in case are refused.
- A store created before the change opens and saves quotes, on SQLite and on a hosted engine.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_form.py::_gives_each_its_own_id`;
`src/haute/_quote_store/_schema.py::structure`; `src/haute/_quote_store/_schema.py::_named`;
`src/haute/_quote_store/_rows.py::_row_cells`; `src/haute/_quote_store/_migrate.py::_plan_columns`;
`src/haute/_workbench_tables.py`; `src/haute/_workbench_app.py`.

### READ-24 — A sheet and its parts are identified by file name, title and number
**Why:**
- A sheet file holds random ids for the sheet (`sheet_sdzj`), each section (`section_0kih`) and each component (`collection_nj2i`).
- The layout file beside it places each section and component by those ids, so a line that moves a component doesn't say which one moved.
- A component written by hand needs the same made-up id in both files.

**Plan:** The owner set this direction on 3 November 2026.
- **The sheet** has no id: its file name identifies it, as for a table.
- **A section** is identified by its title:
  - titles are unique within a sheet, and the editor numbers a new section's title;
  - a duplicate title is refused on load and on save, naming the sheet and the title;
  - renaming a section renames its key in the layout file.
- **A component** is identified by its section's title and a number:
  - the number is one more than the highest number in its section, so the first is 1;
  - it is stored on the component in the sheet file and keys its box in the layout file;
  - adding, removing or reordering components changes only their own lines;
  - a number can come back after the highest component is deleted, which is harmless, since that component's box goes with it;
  - a component moved to another section, or duplicated, takes the next number there;
  - two branches adding to one section can choose the same number, so a duplicate number in a section is refused on load, naming the sheet, the section and the number.
- **The component's place in its section's list** was considered as the number instead. It was rejected because a hand edit that reorders one file and not the other would swap boxes silently.
- **In the editor:** each part gets an id in memory on load, for selection and moves. None reaches a file or a release.
- **Conversion and contract:** `READ-20` converts existing sheets and layouts. The stable-id rationale in the [workbench specification](../workbench/high-level.md) is replaced.

**Acceptance:**
- A sheet and its layout round-trip with no random ids.
- Duplicate section titles and duplicate component numbers are refused.
- Adding or deleting a component changes one entry in each file.
- Renaming a section keeps its layout.
- Moving a component between sections keeps its box.
- Converting the workbench fixture gives the same sheets and positions, and builds the same release twice.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_folder.py::_sheet_and_layout`; `src/haute/_workbench_folder.py::_page`;
`src/haute/_workbench_folder_files.py::LayoutFile`; `src/haute/_workbench_folder_files.py::SectionFile`;
`frontend/src/utils/workbenchForm.ts`.

### READ-25 — Sourced allowed values are stored only as a reference
**Why:** A column with `options_source` also carries a copy of the values from its parameter file. In the investigated pull request, that means 24 columns, 213 lines and all 51 state codes, with numbers stored as text. Adding a value changes two files, or leaves the schema out of date.

**Plan:**
- Write only `options_source` for a sourced column. Allowed values typed into the form, with no source, stay as typed; `WB-38` owns their shape.
- Fill sourced values on read through one reader, from the parameter file at the same git revision as the form, when comparing revisions and when building a release.
- A missing file or column, or a value invalid for the column's type, is refused, naming the file and the column. A historical read never fills values from the working tree.
- A release ships the values filled in, as it already refreshes them.

**Acceptance:**
- The schema files hold no copied values.
- The editor, Preview and a built release show the parameter file's values.
- A form read at an older revision uses that revision's parameter file, which differs from the working tree in the test.
- A missing file, a missing column and an invalid value are each refused.

**Dependencies:** `READ-20`; `WB-38` in the [workbench roadmap](workbench.md) for values with no source.

**Evidence:** `src/haute/_workbench_params.py::with_param_options`;
`src/haute/_workbench_folder.py::form_at_ref`; `src/haute/_workbench_app.py`.

### READ-26 — A rating table is written as a grid
**Why:** Each cell of a rating table is an object over 5 lines, so a 4 by 4 table is 97 lines. Changing one band's label touches 38 lines. In the investigated pull request:
- filling 16 rates took one 82-line hunk;
- an outlier of 0.25 among rates of 0.015 to 0.05 could not be seen;
- a change of factor order that also reset four rates came in a 62-line diff.

**Plan:**
- **The grid:**
  - `factors` lists the table's factors in order (up to three, today's limit);
  - `columns` lists the levels of the last factor;
  - `rows` holds one line per combination of the other factors: their levels first, then one rate per column.
  - With one factor there is no `columns`, and each row is a level and its rate.
- **Missing cells and values:**
  - a cell with no entry is `null`, and a lookup there misses and follows `onMissing`;
  - a rate of 0 is a rate.
- **What is kept:** levels keep their JSON type, so `25` and `"25"` stay distinct. `factorDtypes`, the outputs and the other table settings stay beside the grid.
- **`defaultValue`** is written as a number, next to `onMissing`, so it is clear which applies.
- **Conversion:** a table with duplicate entries is already refused (see the [rating specification](../rating/high-level.md)), so it converts only once fixed.
- **Contract:** replace the row-array contract in the [rating specification](../rating/high-level.md).

**Acceptance:**
- The grid round-trips one, two and three factors, sparse tables, typed levels and every table setting.
- Tests tell a missing cell, a zero and the default apart.
- Changing one rate changes one line.
- The reference pipeline prices the same after conversion.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_rating.py::rating_table_lookup`;
`src/haute/_rating_step_config.py::normalise_rating_step_config`;
`frontend/src/panels/editors/rating/ratingTableUtils.ts::buildCartesianEntries`.

### READ-27 — Banding is written as a list of bands
**Why:** Breakpoint banding is stored as a map from each band's upper edge to its label. Reading it takes knowledge of that convention, and the open-ended top band is spelled `"": "9-10"`.

**Plan:**
- Write one band per line as `{"upper": 4, "label": "1-4"}`, in order.
- The open-ended band has no `upper` and comes last.
- Edges keep today's kinds: numbers, dates and dates with times, written as today.
- Whether a band includes its upper edge stays a setting of the factor (`rightClosed`).
- Drop `"default": null`.
- Categorical banding keeps its map. Identity entries such as `"Frame": "Frame"` stay, because an unlisted value falls to the default and misses the rating.

**Acceptance:**
- Round-trip tests for numeric, date and datetime edges, for bands closed on either side, for an open-ended top band and for categorical maps.
- The reference pipeline prices the same after conversion.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_banding_config.py::compact_banding_config_for_sidecar`.

### READ-28 — Constants are a map from name to value
**Why:** A constant node holds each constant in 4 lines, with its value as text (`"1.45"`); a numeric text becomes a number when it runs. In the investigated pipeline, constants that only formulas read reach them through a cross join. That costs four nodes, four `connect()` calls, two sidecar entries and two positions.

**Plan:**
- Write a constant node as a map from name to value, one per line. A number is a JSON number and text stays text, as each runs today.
- **Promotion to global constants:** the conversion promotes a constant node, turning its constants into global constants and removing the node and its join, only when its structure allows it. All of these must hold:
  - the node's one consumer is a single cross join, with no keys, that adds its columns to every row of one stepped Polars node's input. A keyed join can leave a row without the constant or drop it, so it never qualifies;
  - within that Polars node, every reference to a constant column is a column reference in a formula step outside any group-by or aggregation, where a column and a scalar behave alike;
  - no step assigns to a constant column, so every reference reads the original value;
  - no code step comes after the join, since Haute cannot tell what a code step reads;
  - no constant column reaches the Polars node's output: a later step selects it out or drops it;
  - each constant's name is free among the global constants, and is a valid name there.

  Otherwise the conversion leaves the node and reports which condition failed. The global constant keeps the value's runtime type: a numeric text becomes a number, as it runs today, and text stays text.
- After a promotion, the sample must give every node the same output columns, types and values. This verifies a rewrite the structure allowed; it never decides eligibility.
- Constants that banding or rating steps read stay in their node.
- Trace already shows a global constant's value in a formula.

**Acceptance:**
- The constant node round-trips as a map, with numbers and text.
- Conversion tests leave the node, naming the failed condition, for each of these:
  - a name collision;
  - a constant column reaching the node's output;
  - two consumers;
  - a keyed left or inner join, even when every sampled row matches;
  - a reference inside an aggregation;
  - a code step after the join;
  - a step that overwrites a constant column only on rows the sample doesn't hold, so the sample alone would agree.
- A text constant is promoted as text.
- On the reference pipeline, only formula-only nodes are removed, and it prices the same.
- Trace shows the values.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_node_apply.py`; `src/haute/_config_io.py::global_constants_json`;
`src/haute/_expression_parser.py::_InlineGlobalConstants`.

### READ-29 — Every key Haute's schemas own is snake_case
**Why:** Configs mix camelCase keys (`outputColumn`, `defaultValue`, `onMissing`, `inputMapping`, `maintainOrder`) with snake_case keys (`output_column` on Model Score, `selected_columns`), while `main.py` uses snake_case throughout.

**Plan:**
- Rename every key Haute's schemas own to snake_case:
  - in node configs, the sidecar and the form folder;
  - in the browser's config types, which carry configs as stored;
  - in the assistant's catalogue.
- The specification lists the full map of old to new keys.
- Keys the analyst names never change: column names, categorical levels, source names in `by_source`, constant names, sheet and table names.
- The conversion for this change renames every listed key in one pass, so each file changes once for it.
- Coordinate with `PCFG-R07` (typed config models) and `API-R03` (the generated browser contract), which own those boundaries.

**Acceptance:**
- No schema-owned camelCase key remains in a saved file or schema.
- A camelCase column name, categorical level and source name survive conversion and a save unchanged.
- The assistant's catalogue test and the browser contract match.

**Dependencies:** `READ-20`; `PCFG-R07` in the [pipeline-config roadmap](pipeline-config.md); `API-R03` in the [server API roadmap](server-api.md).

**Evidence:** `src/haute/_config_io.py::node_config_json`; `src/haute/_banding_config.py`;
`src/haute/assistant/_catalog.py`.

### READ-30 — `connect()` only where the signatures cannot say it
**Why:** Of the 16 `connect()` calls in the investigated pipeline:
- 6 carry no port, so they only repeat the signatures;
- 5 carry only `base` or `join`, which is the parameter's position anyway;
- 5 name a Workbench table that the parameter already names.

**Plan:**
- Signatures and edges are bound by one rule ([pipeline-config](../pipeline-config/high-level.md), Binding parameters to inputs), and the parser and the runtime `Pipeline` alike infer an edge from a parameter named after a node. Write `connect()` only for an edge that the signature cannot name exactly:
  - an edge with a port the parameter's place doesn't give;
  - the input a frame hook's decorator consumes, which no parameter names (for example `connect("source", "rate")` for `def rate(df)`).

**Acceptance:**
- The reference pipeline regenerates with only the needed calls, its frame hooks' calls kept.
- `haute run`, a standalone `import main` and a dry-run deploy give every node the same output.

**Dependencies:** None.

**Evidence:** `src/haute/pipeline.py::Pipeline`; `src/haute/_graph_builders.py::_build_edges`.

### READ-31 — A sheet's gaps, conditions and appearance are written plainly
**Why:**
- `"spacers": [3, 5]` counts fields, so a field inserted by hand moves the gap silently.
- `"match": 2` means "at least 2".
- `column_looks` repeats each field's name.

**Plan:**
- **Gaps:** each gap is a `null` in `fields`, at its place. Two in a row make a wider gap, and a gap may come first or last, as `spacers` allows today.
- **Conditions:** `match` keeps `"all"` and `"any"`, and a number becomes `"at_least": n` in its place, for a whole `show_when` and for a group alike.
- **Appearance:** a field with an appearance of its own is written as an object holding the field and its settings, in its place in `fields`.

**Acceptance:**
- Round-trip tests for:
  - a gap first, last and doubled;
  - `all`, `any` and `at_least` on a `show_when` and on a group;
  - a field with and without its own appearance.
- The workbench fixture converts to the same sheet.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_form.py::CollectionWidget`; `src/haute/_workbench_form.py::ConditionGroup`.

### READ-33 — A column or table renamed against the default branch keeps its values
**Why:** After `READ-23`, a column renamed in the form reads as removed and added when compared with the default branch. The store keeps the old SQL column and its values through saves, but the renamed column starts empty. The schema pane already pairs a renamed column with its column when comparing against a database.

**Plan:** If reopened, let the analyst pair a removed column with an added one when comparing against the default branch too, as the pane does against a database. The SQL then renames the column, or the table, so saved quotes keep their values in it.

**Acceptance:** A column renamed and paired keeps every saved value, on SQLite and on the hosted engines.

**Dependencies:** `READ-23`; the owner's decision to reopen it.

**Evidence:** `src/haute/_quote_store/_migrate.py::_plan_columns`.

### READ-40 — Generated workflows declare each secret, setup and check once
**Why:** In the generated workflows:
- `python-version` appears seven times, though uv reads `.python-version`;
- the four `AZURE_*` secrets are repeated in three steps;
- `release.yml` copies the checks in `ci.yml`, and `ci.yml` repeats its setup in four jobs.

**Plan:**
- Declare the secrets once per job.
- Drop `python-version` where uv reads it.
- Make `ci.yml` a reusable workflow that `release.yml` calls, so the checks are written once.
- Keep every behaviour the workflows have today:
  - publishing by image digest;
  - staging rollouts one at a time;
  - production gated on staging;
  - the reports kept as artifacts.
- Shell plumbing such as `tail -n 1` and `tee publish.log` stays until Haute's commands give their results directly. `WB-08` owns those commands for a workbench project.
- `DOC-02` updates the CI pages to match.

**Acceptance:**
- Golden tests of the generated workflows.
- A structural test: publishing needs every check, and production needs staging.
- A generated project's CI on GitHub stops before publishing when a check fails.

**Dependencies:** `WB-08` in the [workbench roadmap](workbench.md); `DOC-02` in the [documentation roadmap](docs.md).

**Evidence:** `src/haute/_scaffold.py::_github_ci_yml`; `src/haute/_scaffold.py::_github_deploy_yml`;
`src/haute/_scaffold.py::_github_secrets_env`.

### READ-41 — A new project holds no `uv init` placeholders and no default settings
**Why:**
- A new project keeps leftovers from `uv init`: an empty README, `"Add your description here"`, `[project.scripts]` and `src/<project>/`.
- `haute.toml` writes settings at their default values (`[test_quotes] dir`, `port = 8080`).
- `.env.example` doesn't say what the `AZURE_*` values are for.
- The generated `.gitignore` repeats its `# Haute` header.

**Plan:**
- **`uv init` leftovers:** on a first `haute init` only, remove those that are still exactly as uv wrote them and that nothing references, keeping a working build configuration. A re-run never removes a file.
- **Settings:** write only those that differ from their defaults.
- **`.env.example`:** explain each entry in a comment.
- **`.gitignore`:** write the header once.
- The quote store's `provider` stays in both `haute.toml` and `connections.toml`. Releases read only `haute.toml`, and a local connection is checked against it.

**Acceptance:**
- A scaffold test of a new workbench project finds none of the leftovers and no settings at their defaults, and the project builds.
- A re-run on a project whose README and `src/` were written by hand keeps them.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::write_generated_files`; `src/haute/_scaffold.py`.
