# Readable output roadmap

## Scope

These packages cover what Haute writes into a project's repository, read as a diff:
- `main.py` and its `.haute.json` sidecar;
- the node configs under `config/` and the global constants;
- the Workbench form folder (`workbench/`) and the test quotes;
- the files `haute init` scaffolds.

The aim is that a reviewer who never opens the editor can follow a pull request, and that an analyst can develop in code. One change in the editor should make a small diff that says what changed, and a hand edit to a file should survive the next load and save.

The packages come from an investigation of [commercial-pricing pull request #1](https://github.com/PricingFrontier/commercial-pricing/pull/1) on 3 November 2026. It measured:
- **Polars nodes are stored twice.** The body in `main.py` is what runs, and an expression tree in `config/polars/<node>.json` holds the steps again. Adding one term to a 12-term sum changes 1 line of Python and 39 lines of JSON.
- **Six JSON writers follow different rules.** Rewriting the pull request's 41 JSON files under one rule, with no change to what they hold, took them from 2,990 lines to 1,652.
- **A rating table takes 5 lines per cell.**
- **Inserting a node into `main.py` moves unrelated functions.**
- **Random ids fill the Workbench form folder**, such as `c_818cc52f3c8b78a9`, `t_d7d9abf03a7cf621` and `collection_nj2i`.

The owner decided the following on 3 November 2026:
- **Polars code lives only in `main.py`.** Haute rebuilds as many steps as it can from the code. A statement it cannot recognise becomes a code step on its own; the rest of the node stays as steps.
- **Workbench columns and tables have no ids.** Their names identify them. How the quote store's SQL follows a renamed column or table is left to a later package (`READ-33`).
- **Sheets, sections and components have no random ids.** A sheet is identified by its file name, a section by its title, and a component by a number given once when it is created (`READ-24`).
- **`main.py` keeps its function order.** A new node's function goes just above the first function that uses it.
- **Formats:**
  - rating tables are written as a grid;
  - constants are a name-to-value map, and constants that only formulas read become global constants;
  - keys on disk are snake_case;
  - generated workflows run named Haute commands.

**Out of scope:** `haute init` rewrites `haute.toml` from scratch on a re-run, as decided on 1 November 2026, so editing that file in place is not planned.

The packages fall into three groups:
- **Defect fixes:** `READ-01` to `READ-04`.
- **Writing changes:** `READ-10` to `READ-13` change how files are written but not what they hold. The next save rewrites each file once, and no conversion is needed.
- **Format changes:** `READ-20` onwards. The loader accepts only the current format ([specification governance](../README.md)), so each change lands together with its conversion in `READ-20`, in a commit of its own.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| READ-01 | Planned | P1 | Every node type's parameters are checked against its inputs, and the parser and a standalone run wire a node the same way. |
| READ-02 | Planned | P2 | A module docstring written by hand in `main.py` is kept on save. |
| READ-03 | Planned | P2 | `haute init` writes LF files on every platform and scaffolds a `.gitattributes`. |
| READ-04 | Planned | P3 | Git ignores `test_quotes_report.md`. |
| READ-10 | Planned | P2 | One printer writes every committed JSON file, with a fixed shape per kind of record. |
| READ-11 | Planned | P2 | Node positions are whole numbers, on one line per node. |
| READ-12 | Planned | P2 | Adding or removing a node moves no other function in `main.py`. |
| READ-13 | Planned | P3 | Generated code leaves out a default it repeats and a comment that says nothing, and new nodes get plain names. |
| READ-20 | Planned | P2 | A project in an older format is converted to the current format in one step that changes nothing else. |
| READ-21 | Planned | P2 | A prototype proves that Haute can rebuild every kind of Polars step from the code it renders. |
| READ-22 | Planned | P2 | A Polars node is stored only as code in `main.py`, and a hand edit or comment keeps the rest of its steps. |
| READ-23 | Planned | P2 | Workbench columns and tables are identified by their names, with no ids in the form folder. |
| READ-24 | Planned | P2 | Sheets, sections and components are identified by name and number, not by random ids. |
| READ-25 | Planned | P2 | A column's allowed values are stored only as a reference to their parameter file. |
| READ-26 | Planned | P2 | A rating table is written as a grid, one row of the table per line. |
| READ-27 | Planned | P3 | Banding is written as a list of bands, each with its upper edge and its label. |
| READ-28 | Planned | P3 | Constants are a map from name to number, and constants that only formulas read become global constants. |
| READ-29 | Planned | P3 | Every key in a saved file is snake_case. |
| READ-30 | Planned | P3 | `main.py` holds a `connect()` call only for an edge the signatures cannot express. |
| READ-31 | Planned | P3 | Gaps, conditions and appearance on a sheet are written where a reader looks for them. |
| READ-32 | Planned | P3 | Node configs that hold nothing have no file. |
| READ-33 | Deferred | P3 | The quote store keeps a renamed column's or table's saved values. |
| READ-40 | Planned | P3 | Generated workflows are short, named steps that each run one Haute command. |
| READ-41 | Planned | P3 | `haute init` leaves no placeholder files and writes no default settings. |

## Planned improvements

### READ-01 — Every node type's parameters are checked against its inputs
**Why:** Only Polars nodes have their parameters checked against the edges into them. For every other node type the check is skipped ("Non-Polars node types have no authored signature and are skipped"), a reason older than the declarations of 26 September 2026. The investigation tested three cases:
- a typo in a parameter is ignored;
- a parameter naming one node while `connect()` names another gives the node both edges, with no error;
- a parameter naming a Workbench table that has no `connect()` call gives no edge and no error.

The parser infers edges from parameter names, but the runtime `Pipeline` takes edges only from `connect()`. So `haute run` and a plain `import main; pipeline.run()` can wire the same node differently.

**Plan:**
- Check every node type's parameters against its edges when the pipeline is parsed, as Polars nodes are checked.
- Refuse three mismatches, naming the node and the parameter:
  - a parameter that names no input;
  - an input that no parameter names;
  - a parameter that disagrees with its `connect()` call.
- Bind ports and `df` hooks the same way in the parser and in a standalone run.
- Land this in a commit of its own, before `READ-30`.

**Acceptance:**
- A parser test for each of the three mismatches on a non-Polars node type, each refused with the node and the parameter named.
- A standalone run of the reference pipeline records the same edges as the parser.

**Dependencies:** None.

**Evidence:** `src/haute/_parser_bindings.py::assert_polars_parameters_bound`;
`src/haute/_graph_builders.py::_build_edges`; `src/haute/pipeline.py::Pipeline`.

### READ-02 — A hand-written module docstring is kept on save
**Why:** Codegen writes its own module docstring at the top of `main.py` and replaces one the analyst wrote, without notice. It is the only hand edit lost silently. Also, `haute init` writes a different docstring from codegen's, so a new project's first save always rewrites it.

**Plan:**
- Keep the module docstring the file holds, and write codegen's only when there is none.
- Make the scaffolded `main.py` start with the docstring codegen would write.

**Acceptance:**
- A codegen round-trip test keeps a hand-written docstring byte for byte.
- A project fresh from `haute init` saves with no change to its docstring.

**Dependencies:** None.

**Evidence:** `src/haute/codegen.py::_render_module`; `src/haute/_scaffold.py::starter_pipeline`.

### READ-03 — `haute init` writes LF files and a `.gitattributes`
**Why:** Scaffold files are written with text-mode `write_text`, which gives CRLF line endings on Windows, the `#!/bin/sh` pre-commit hook included. The form folder, by contrast, writes LF on purpose. So a project made on Windows mixes line endings. Also, `uv.lock` (587 lines in the investigated pull request) is the largest file in a new project's first diff.

**Plan:**
- Write every scaffold file as UTF-8 bytes with LF line endings, through one writer.
- Scaffold a `.gitattributes`:
  - `eol=lf` for the text formats Haute writes (`*.py`, `*.json`, `*.toml`, `*.yml`, `*.md`);
  - `uv.lock linguist-generated=true`, so GitHub collapses the lock file in pull requests.
- Leave CSV files out of `.gitattributes`: analysts save parameter files from other tools.

**Acceptance:**
- A scaffold test finds no carriage return in any file `haute init` writes, on Windows as well.
- The generated `.gitattributes` names only the listed formats.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::write_generated_files`; `src/haute/_scaffold.py`.

### READ-04 — Git ignores the test quotes report
**Why:** `haute app build` writes `test_quotes_report.md` at the project root, and CI keeps it as an artifact. The gitignore guard doesn't list it, so after every local build it shows up as an untracked file, ready to be committed by mistake.

**Plan:** Add the report to the gitignore guard's entries.

**Acceptance:** The guard test expects the entry, and a project the guard has run on ignores the report.

**Dependencies:** None.

**Evidence:** `src/haute/_gitignore_guard.py::GITIGNORE_GUARD_ENTRIES`;
`src/haute/_workbench_test_quotes.py::REPORT_FILE`.

### READ-10 — One printer writes every committed JSON file
**Why:** Six writers follow four key-order rules and two rules for defaults:
- node configs sort only their top-level keys and write empties and nulls;
- global constants have a writer of their own;
- the sidecar writes positions as 17-digit floats;
- the Workbench folder puts only all-number objects on one line;
- test quotes use plain `json.dumps`.

Rewriting the investigated pull request's 41 JSON files under one rule took them from 2,990 lines to 1,652 (measured with a 100-column width limit). Two more problems:
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
- Order keys by a fixed rule per kind, with the identifying key first. Sort maps whose order means nothing, and keep the analyst's data in the order it was written.
- Leave defaults out according to each schema's own rules, not a generic "drop empties" pass.
- Keep the release form's serialisation (`render_form`) separate: it is what a release ships, not a source file.

**Acceptance:**
- A golden test for each kind of file.
- Saving twice gives identical bytes.
- Every reader loads the reference pipeline and the workbench fixture to the same values before and after.
- Rewriting a project changes only whitespace and key order.

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
- Find the cause of the 1 to 3 pixel moves.
- One line per node comes from `READ-10`.

**Acceptance:**
- A store test: a drag ending at fractional coordinates stores whole numbers.
- Loading and saving an unchanged pipeline leaves its sidecar byte for byte the same.

**Dependencies:** `READ-10` for the one-line shape.

**Evidence:** `frontend/src/stores/useGraphStore.ts::positionsEqual`;
`src/haute/routes/_helpers.py::save_sidecar`.

### READ-12 — Adding or removing a node moves no other function in `main.py`
**Why:** Functions are written in layers by distance from the inputs. Inserting a node pushes everything below it one layer deeper and reshuffles functions that did not change. One commit in the investigated pull request moved two unchanged functions. The `br_` and `ce_` chains are also interleaved, because the layers mix them.

**Plan:**
- Keep the function order the file has.
- A new node's function goes just above the first function that uses its output, or at the end when nothing does yet.
- A removed node's function goes, and nothing else moves.
- A one-off regrouping of existing files is not planned.

**Acceptance:** Codegen tests on the reference pipeline:
- inserting, removing and renaming a node each change only that node's function and its own `connect()` lines;
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
- A frontend test that a pipeline's first Workbench Output is named `workbench_output`.

**Dependencies:** None.

**Evidence:** `src/haute/_source_layout.py::WIRE_COMMENT`;
`frontend/src/utils/flowElements.ts::nodeLabel`.

### READ-20 — A project in an older format is converted in one step
**Why:** The loader accepts only the current format and refuses older ones. Each format change below therefore needs a way to bring an existing project across. Today, old pipeline files are converted node by node through the editor's Recover settings.

**Plan:**
- Write one conversion for each format change, kept together in one module.
- When the editor opens a project in an older format, it converts the project in one step and names the files it rewrote. The same code runs as `haute migrate` for scripted use.
- A conversion changes nothing beyond the format, so it can go in a commit of its own.
- Prove each conversion three ways:
  - the converted files load to the same values;
  - saving twice gives identical bytes;
  - the sample prices the same before and after.
- Convert `main` before other branches move on. While two branches hold different formats, a comparison between them sees every changed record as removed and added again.
- Cover any new file that a conversion creates in the deploy bundle.

**Acceptance:**
- For each format change, a test converts a fixture in the old format and holds those three properties.
- The command reports the files it rewrote and exits with an error on a project it cannot convert.

**Dependencies:** None. Every package from `READ-22` onwards that changes a file format depends on it.

**Evidence:** `specs/README.md`; `src/haute/routes/pipeline.py::apply_recover_unavailable_node`.

### READ-21 — Every Polars step kind is rebuilt from the code it renders
**Why:** Storing a Polars node only in `main.py` (`READ-22`) depends on Haute rebuilding steps from code: about 1,000 lines that reverse the step renderer. Some forms are ambiguous:
- a pivot and a filtered group-by render to the same code;
- two code steps side by side merge;
- the `divide` pattern has to be recognised.

The [syntax-boundary specification](../codegen/structured-syntax-boundary.md) does not rule this out. It rejects a graph model taking authority over arbitrary Python, not rebuilding the steps Haute supports from the code it writes.

**Plan:**
- Before anything changes on disk, build the step rebuilder as a prototype. Check that rebuilding the rendered code of every step kind in the catalogue, alone and in chains, gives the original steps back.
- Pick one form for each ambiguous case and rebuild it as that step.
- Find what the prototype cannot rebuild. A form Haute renders but cannot rebuild is a defect, unless the kind changes how it renders.

**Acceptance:** A property test over the step catalogue passes: rebuilding rendered steps gives the same steps. Every step kind is covered, and the list of ambiguous forms with their chosen step is recorded in the specification.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_steps.py::_Renderer`; `src/haute/_polars_steps.py::render_node_steps`;
`src/haute/_config_builder.py::_reconcile_steps`.

### READ-22 — A Polars node is stored only as code in `main.py`
**Why:** Today the code in `main.py` runs, and `config/polars/<node>.json` holds the steps again as an expression tree. Steps survive only if their rendering matches the code exactly, comments included. That causes several problems:
- the tree nests one level per operator, so a sum is capped at 13 terms (`MAX_EXPR_DEPTH = 12`);
- adding a term changes 39 lines of JSON;
- editing the JSON by hand discards the steps;
- editing the code by hand, or adding a comment to it, turns the whole node into code.

**Plan:**
- Drop `config/polars/*.json` and the `config=` argument on `@pipeline.polars`.
- On load, rebuild the steps from the node's code (`READ-21`). A statement Haute cannot recognise becomes a code step on its own, and the statements around it stay steps.
- Comments stay where they are and don't turn a node into code.
- A chain of the same operator becomes one step with a list of terms, so the 13-term cap goes. Grouping and the order of evaluation are kept: floats are never reassociated, and `sum_horizontal` is never substituted, since it treats missing values differently.
- Steps get ids on load, from their kind and position. These ids must stay stable for the assistant's `edit_steps` while one load is in use.
- The formula box shows each formula from the rebuilt step, so Haute needs no second implementation of the formula language.
- `READ-20` converts existing projects by deleting the JSON once the code rebuilds to the same steps. A node that doesn't rebuild exactly is reported, and its code is kept.

**Acceptance:**
- Loading and saving a node's code is byte-stable.
- A hand edit to one statement keeps the other steps.
- A comment keeps the steps.
- A 20-term sum is one step.
- The formula box round-trips each formula.
- The reference pipeline prices the same before and after its conversion.

**Dependencies:** `READ-20`, `READ-21`.

**Evidence:** `src/haute/_config_builder.py::_same_program`; `src/haute/_polars_steps.py::MAX_EXPR_DEPTH`;
`frontend/src/panels/editors/polarsSteps/formula.ts::additive`; `src/haute/assistant/_catalog.py`.

### READ-23 — Workbench columns and tables are identified by their names
**Why:** Every column in `schema/*.json` carries a random id (`"id": "c_818cc52f3c8b78a9"`, in two widths), and every table carries one too (`t_d7d9abf03a7cf621`). The sheets already refer to fields by name (`br_jobsites.limit`). The ids have three uses:
- they let a comparison against `main` treat a rename as a rename;
- they key cells in memory;
- they are hashed into the store's constraint names.

The owner judged that this complexity isn't worth it.

**Plan:**
- Remove the column and table ids from the form, the form folder and the editor. A table's name, which is its file name, identifies it; a column's name identifies it within its table.
- Key cells, comparisons and the editor's selection by name.
- Derive constraint and index names from the table's name.
- Renames:
  - a renamed column reads as a column removed and a column added, and a renamed table as a table removed and a table added;
  - the store never drops the removed column, so quotes saved before the rename keep their values there, and the renamed column starts empty;
  - `READ-33` owns following a rename.
- Two one-off consequences:
  - The release id changes once, because a release's id covers the bytes of the form it ships.
  - A store created before this change still carries the old constraint names, so it is created again. No production store exists yet.
- `READ-20` removes the ids from existing forms.

**Acceptance:**
- No id remains in the form model or the folder files.
- The store comparison and the cell reader work by name.
- A renamed column shows as one column removed and one added.
- Converting the workbench fixture gives the same form, apart from the ids.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_form.py::_gives_each_its_own_id`;
`src/haute/_quote_store/_schema.py::structure`; `src/haute/_quote_store/_schema.py::_named`;
`src/haute/_quote_store/_rows.py::_row_cells`; `src/haute/_quote_store/_migrate.py::_plan_columns`;
`src/haute/_workbench_app.py`.

### READ-24 — Sheets, sections and components are identified by name and number
**Why:**
- A sheet file holds random ids for the sheet (`sheet_sdzj`), each section (`section_0kih`) and each component (`collection_nj2i`).
- The layout file beside it places each section and component by those ids, so a line that moves a component doesn't say which one moved.
- A component written by hand needs the same made-up id in both files.

**Plan:** The owner set this direction on 3 November 2026.
- The sheet has no id: its file name identifies it, as for a table.
- A section is identified by its title. Titles must be unique within a sheet: the editor numbers a new section's title, and a duplicate is refused on save, naming the sheet and the title. Renaming a section changes its key in the layout file too.
- A component is identified by its section's title and a number given once, when the component is created:
  - The first component is 1, the next 2, and so on. Numbers are never reused or renumbered.
  - The number is stored on the component in the sheet file and keys its box in the layout file. So adding, removing or reordering components changes only their own lines.
  - The component's place in its section's list was considered instead. It was rejected because a hand edit that reorders one file and not the other would swap boxes silently.
- The editor gives each part an id in memory on load for selection and display; none reaches disk.
- `READ-20` converts existing sheets and layouts.

**Acceptance:**
- A sheet and its layout round-trip with no random ids.
- A duplicate section title is refused.
- Adding or deleting a component changes one entry in each file.
- Converting the workbench fixture gives the same sheets and positions.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_folder.py::_sheet_and_layout`; `src/haute/_workbench_folder.py::_page`;
`src/haute/_workbench_folder_files.py::LayoutFile`; `src/haute/_workbench_folder_files.py::SectionFile`.

### READ-25 — Allowed values are stored only as a reference
**Why:** A column with `options_source` also carries a copy of the values from its parameter file. In the investigated pull request, that means 24 columns, 213 lines and all 51 state codes, with numbers stored as text. Adding a value changes two files, or leaves the schema out of date.

**Plan:**
- Write only `options_source`.
- Fill the values on read from the parameter file at the same git revision as the form, both when comparing revisions and when building a release. A release ships the values filled in.

**Acceptance:**
- The schema files hold no copied values.
- The editor, Preview and a built release show the parameter file's values.
- A form read at an older revision uses that revision's parameter file.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_params.py::with_param_options`;
`src/haute/_workbench_folder.py::form_at_ref`.

### READ-26 — A rating table is written as a grid
**Why:** Each cell of a rating table is an object over 5 lines, so a 4 by 4 table is 97 lines. Changing one band's label touches 38 lines. In the investigated pull request:
- filling 16 rates took one 82-line hunk;
- an outlier of 0.25 among rates of 0.015 to 0.05 could not be seen;
- a change of factor order that also reset four rates came in a 62-line diff.

The engine already converts each level to its column's type before matching.

**Plan:**
- Write each table as a grid: a header naming the factors and their levels, then one line per row of the table.
- Use one shape for one, two, and three or more factors, with sparse tables allowed.
- Keep every level's type and every rate exactly.
- When two entries share the same levels, refuse the table and name them. Today the last one silently wins.
- Write `defaultValue` as a number, in a fixed place next to `onMissing`, so it is clear which applies.

**Acceptance:**
- The grid round-trips every table shape, including sparse tables and three factors, with typed levels.
- Duplicate keys are refused.
- Changing one rate changes one line.
- The reference pipeline prices the same after conversion.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_rating.py::rating_table_lookup`;
`src/haute/_rating_step_config.py::normalise_rating_step_config`;
`frontend/src/panels/editors/rating/ratingTableUtils.ts::buildCartesianEntries`.

### READ-27 — Banding is written as a list of bands
**Why:** Banding is stored as a map from each band's upper edge to its label. Reading it takes knowledge of that convention, and the open-ended top band is spelled `"": "9-10"`.

**Plan:**
- Write one band per line, such as `{"to": 4, "label": "1-4"}`, with the open-ended band spelled out.
- Drop `"default": null`.
- Keep identity maps such as `"Frame": "Frame"`. An unlisted value falls to the default and misses the rating, so dropping them would need a pass-through setting first.

**Acceptance:**
- A round-trip test for numeric bands, an open-ended top band and categorical maps.
- The reference pipeline prices the same after conversion.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_banding_config.py::compact_banding_config_for_sidecar`.

### READ-28 — Constants are a map from name to number
**Why:** A constant node holds each constant in 4 lines, with its value as text (`"1.45"`), while global constants are numbers. In the investigated pipeline, constants that only formulas read reach them through a cross join. That costs four nodes, four `connect()` calls, two sidecar entries and two positions.

**Plan:**
- Write a constant node as a map from name to number, one per line.
- When every consumer of a constant node is a formula, `READ-20` turns its constants into global constants and removes the node and its join.
- Constants that banding or rating steps read stay in their node.
- Trace already shows a global constant's value in a formula.

**Acceptance:**
- The constant node round-trips as a map.
- Converting the reference pipeline removes only formula-only constant nodes and prices the same.
- Trace shows the values.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_config_io.py::global_constants_json`;
`src/haute/_expression_parser.py::_InlineGlobalConstants`.

### READ-29 — Every key in a saved file is snake_case
**Why:** Configs mix camelCase keys (`outputColumn`, `defaultValue`, `onMissing`, `inputMapping`, `maintainOrder`) with snake_case keys (`output_column` on Model Score, `selected_columns`), while `main.py` uses snake_case throughout.

**Plan:**
- Use snake_case for every key on disk and in the API, for every node type, in the frontend and in the assistant's catalogue.
- Convert in the same commit as the other format changes, so each file changes only once.

**Acceptance:**
- No camelCase key remains in any saved file or schema.
- The assistant's catalogue test and the generated browser contract match.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_config_io.py::node_config_json`; `src/haute/assistant/_catalog.py`.

### READ-30 — `connect()` only where the signatures cannot say it
**Why:** Of the 16 `connect()` calls in the investigated pipeline:
- 6 carry no port, so they only repeat the signatures;
- 5 carry only `base` or `join`, which is the parameter's position anyway;
- 5 name a Workbench table that the parameter already names.

**Plan:**
- Once `READ-01` makes signatures and edges agree everywhere, write `connect()` only for an edge with a port that the signature cannot express.
- The runtime `Pipeline` takes the other edges from the signatures, as the parser does.

**Acceptance:**
- The reference pipeline regenerates with only the needed calls.
- A standalone run and `haute run` record the same edges.

**Dependencies:** `READ-01`.

**Evidence:** `src/haute/pipeline.py::Pipeline`; `src/haute/_graph_builders.py::_build_edges`.

### READ-31 — A sheet's gaps, conditions and appearance are written plainly
**Why:**
- `"spacers": [3, 5]` counts fields, so a field inserted by hand moves the gap silently.
- `"match": 2` means "at least 2".
- `column_looks` repeats each field's name.

**Plan:**
- Write a gap as a marker inside `fields`.
- Spell the condition as `at_least`.
- Put a field's appearance settings on its own entry.

**Acceptance:** Round-trip tests for each setting, and the workbench fixture converts to the same sheet.

**Dependencies:** `READ-20`.

**Evidence:** `src/haute/_workbench_form.py`.

### READ-32 — Node configs that hold nothing have no file
**Why:** Some files exist only because the node type requires a `config=` file, such as `workbench_input/Workbench_Input_1.json` and `workbench_output/Workbench_Output_4.json`, each holding `{}`. An empty `combinedOutputs: []` is also written.

**Plan:**
- Make the config file optional for node types whose config can be empty, and write none when it is.
- Leave `combinedOutputs` out when it is empty.

**Acceptance:**
- An empty Workbench Input and Workbench Output save no file and load the same.
- The reference pipeline holds no `{}` file.

**Dependencies:** `READ-20`, `READ-10`.

**Evidence:** `src/haute/_config_io.py::node_config_json`.

### READ-33 — The quote store follows a renamed column or table
**Why:** After `READ-23`, a renamed column reads as a column removed and a column added. The store keeps the old SQL column with the saved quotes' values, and the renamed column starts empty. A renamed table behaves the same way.

**Plan:** If reopened, decide how the store's SQL plan recognises a rename, for example by asking the analyst to pair a removed column with an added one in the schema pane's comparison. Then rename the SQL column or table so that saved quotes keep their values.

**Acceptance:** A renamed column keeps saved values in the store, on SQLite and on the hosted engines.

**Dependencies:** `READ-23`; the owner's decision to reopen it.

**Evidence:** `src/haute/_quote_store/_migrate.py::_plan_columns`.

### READ-40 — Generated workflows run named Haute commands
**Why:** In the generated workflows:
- `python-version` appears seven times, though uv reads `.python-version`;
- the four `AZURE_*` secrets are repeated in three steps;
- `release.yml` copies the checks in `ci.yml`, and `ci.yml` repeats its setup in four jobs;
- shell plumbing such as `tail -n 1` and `tee publish.log` sits in the steps.

**Plan:**
- Each step runs one named Haute command, and the plumbing moves inside the commands.
- Declare the secrets once per job.
- Drop `python-version` where uv reads it.
- Make `ci.yml` a reusable workflow that `release.yml` calls.
- Coordinate with `WB-08`, which owns what a workbench project's CI runs.

**Acceptance:**
- Golden tests of the generated workflows.
- A generated project's CI passes on GitHub with the new files.

**Dependencies:** `WB-08` in the [workbench roadmap](workbench.md).

**Evidence:** `src/haute/_scaffold.py::_github_ci_yml`; `src/haute/_scaffold.py::_github_deploy_yml`;
`src/haute/_scaffold.py::_github_secrets_env`.

### READ-41 — `haute init` leaves no placeholders and writes no defaults
**Why:**
- A new project keeps leftovers from `uv init`: an empty README, `"Add your description here"`, `[project.scripts]` and `src/<project>/`.
- `haute.toml` writes defaults (`[test_quotes] dir`, `port = 8080`).
- `.env.example` doesn't say what the `AZURE_*` values are for.
- The quote store's `provider` is repeated in `haute.toml` and `connections.toml`.
- The generated `.gitignore` repeats its `# Haute` header.

**Plan:**
- Remove the `uv init` leftovers that Haute doesn't use.
- Write only settings that differ from their defaults.
- Explain each `.env.example` entry in a comment.
- Keep the store's `provider` in one file.
- Write the `.gitignore` header once.

**Acceptance:** A scaffold test of a new workbench project finds none of the leftovers, no default-valued settings and one `provider`.

**Dependencies:** None.

**Evidence:** `src/haute/cli/_init_cmd.py::write_generated_files`; `src/haute/_scaffold.py`.
