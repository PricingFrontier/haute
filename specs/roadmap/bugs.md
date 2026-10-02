# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. These packages came from the 27 September 2026 audit of the
Getting Started and Building Models documentation, which checked every page
against the code, and from the review of that documentation on 28 September
(`BUG-10`, `BUG-11`): each one is a place where the code, not only the page, is
wrong. `BUG-12` to `BUG-17` came from reading the parser, the code generator,
the executors, the deploy scorer and the editor's request builders for the
[global constants](global-constants.md) specification and its review on
2 October 2026.
Current rating behaviour is specified in
[the rating specification](../rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-01 | Planned | P1 | Editing a Rating Step in the editor keeps each table's `onMissing` setting. |
| BUG-02 | Decision | P1 | A rating table built in the editor no longer prices an unmatched level at 1.0 without saying so. |
| BUG-03 | Planned | P3 | The ratebook optimiser settings nothing reads are gone. |
| BUG-04 | Planned | P2 | A pickled XGBoost or LightGBM model loads in a Load File node, or is refused by name. |
| BUG-05 | Planned | P3 | The Data Input editor describes the Databricks query field as the SELECT clause it is. |
| BUG-06 | Planned | P2 | A Delta table folder can be chosen as a Lakehouse Data Input in the editor. |
| BUG-07 | Planned | P3 | The Load File picker offers only files a File Type can load. |
| BUG-08 | Planned | P3 | A table added to a Quote Input by hand starts with a valid label. |
| BUG-10 | Planned | P2 | A CSV Data Input's detected schema is read with the node's reader arguments. |
| BUG-11 | Planned | P3 | Setting every Source Switch input back to `-` returns the node to passing through its first input. |
| BUG-12 | Planned | P1 | A save never drops a statement written after the pipeline constructor; such a statement is reported and saving waits for it to move. |
| BUG-13 | Planned | P2 | Deployed External File code sees the preamble's names, as it does in the editor. |
| BUG-14 | Planned | P2 | Model Score code sees the preamble's names on the canvas and when deployed, as it does standalone. |
| BUG-15 | Decision | P2 | A bare `pipeline.run()` routes a source the pipeline has instead of a `batch` scenario no pipeline declares. |
| BUG-16 | Planned | P2 | The optimiser's estimate, auto-range and solve requests carry the preamble, as every other execution request does. |
| BUG-17 | Decision | P3 | A standalone run of a pipeline with a submodel runs it, or refuses up front naming the submodel, instead of reporting it as an unknown node. |

## Planned improvements

`BUG-02` decides when `onMissing` matters for a table built in the editor, so
`BUG-01`'s control is easiest to place after it; the fix that stops the
setting being lost does not wait for it.

### BUG-01 — Editing a Rating Step keeps each table's `onMissing`
**Why:** A rating table's `onMissing` decides what a miss does when the table
has no usable `defaultValue`: `"error"` (the default) raises
`RatingTableMissError`, and `"neutral"` leaves the output null and logs the
misses. The editor has no control for it, and `normaliseRatingTable` rebuilds
every table from `factors`, `factorDtypes`, `outputColumn`, `defaultValue` and
`entries` only. Any edit of the node in the editor (a factor, an entry, the
default, adding or removing a table) therefore removes an `onMissing` set in
the pipeline, with no message, and a table that was set to `"neutral"` fails
the run on its next miss. The same rebuild gives a table with no
`defaultValue` the default `"1.0"`, so a table written to fail on a miss
starts pricing misses at 1.0 after an unrelated edit. Before any edit, the
Default field already shows `1.0` for a table whose `defaultValue` is `null`.

**Plan:** Carry `onMissing` through `normaliseRatingTable`, keep an absent
`defaultValue` absent, and show `onMissing` in the table editor beside Default,
where it applies only while Default is empty.

**Acceptance:** A frontend test edits an entry of a table configured with
`onMissing: "neutral"` and no `defaultValue`, and the committed config still
has that `onMissing` and no `defaultValue`; the editor shows the setting and
changes it; the rating specification names the control.

**Dependencies:** None to stop the loss; the control's placement follows
`BUG-02`.

**Evidence:** `frontend/src/panels/editors/rating/ratingTableUtils.ts::normaliseRatingTable`;
`src/haute/_rating.py::_normalise_on_missing`;
`src/haute/_rating.py::RatingTableMissError`; `specs/rating/high-level.md`
(miss precedence).

### BUG-02 — A rating table built in the editor says when a level is unmatched
**Why:** A miss is filled silently by a usable `defaultValue`, and raises only
when the table has none and `onMissing` is `"error"`. Every table the editor
creates starts with `defaultValue` `"1.0"` (the Rating Step node defaults and
the Add table button), so an unmatched level (a new vehicle group, a key typed
differently from the data) is priced at 1.0 with no error or warning unless
the analyst clears Default. That contradicts fail-loud pricing, and the Rating
Step page's "Misses fail loudly by default".

**Plan:** Decide between: new tables start with an empty Default, so a miss
fails until the analyst sets a default or chooses `"neutral"`; or tables keep
1.0 and the preview and trace report each table's miss count. Then change the
node defaults, the editor, the rating specification and the Rating Step page
together.

**Acceptance:** A new table built in the editor, run on data with an
unmatched level, either fails the run naming the table and key or shows the
table's miss count in the preview; a test covers that miss.

**Dependencies:** None.

**Evidence:** `src/haute/node_defaults.json` (`ratingStep`);
`frontend/src/panels/editors/RatingStepEditor.tsx` (the table fallback and
`addTable`); `src/haute/_rating.py::RatingTableMissError`;
`docs/building-models/nodes/rating-step.md`.

### BUG-03 — Remove the ratebook optimiser settings nothing reads
**Why:** The optimiser config declares `candidate_min`, `candidate_max`,
`candidate_steps` and `structure_mode`. The cache classifies them, recovery
validates `candidate_steps`, and the Optimiser page lists the first three as
required, but the ratebook solve builds `price_contour`'s `RatebookOptimiser`
without any of them, and no editor field sets them. The candidate values come
from the scored scenario grid and the factor structure from the Factors pane,
so a value written for these keys has no effect.

**Plan:** Delete the four fields from `OptimiserConfig`, the cache field
classification, the recovery validation, and the Optimiser page.

**Acceptance:** No reference to the four keys remains in `src/`,
`frontend/src/`, `specs/` or `docs/`; the config round-trip and cache field
classification tests pass.

**Dependencies:** None.

**Evidence:** `src/haute/_types.py` (`OptimiserConfig` and its field list);
`src/haute/_cache.py` (the optimiser field classification);
`src/haute/_node_config_recovery.py`;
`src/haute/routes/_optimiser_solver.py` (`RatebookOptimiser(`);
`docs/building-models/nodes/optimiser.md`.

### BUG-04 — A pickled XGBoost or LightGBM model loads in a Load File node
**Why:** Load File reads pickle and joblib files through an exact allowlist of
classes. The list names XGBoost's `XGBRegressor`, `XGBClassifier` and
`XGBModel` and LightGBM's `LGBMRegressor`, `LGBMClassifier` and `LGBMModel`,
but not the booster each of them pickles inside itself
(`xgboost.core.Booster`, `lightgbm.basic.Booster`). Loading a pickled
`XGBRegressor` or `LGBMRegressor` through `safe_unpickle` fails with "Blocked
unpickling of xgboost.core.Booster" (or `lightgbm.basic.Booster`), so the
listed entries can never load and the list claims support that does not
exist. The Load File page now says these pickles fail.

**Plan:** Decide whether Load File supports these models. If it does, add the
two booster classes (and whatever else their pickles reference) and prove a
pickled regressor and classifier of each library load and predict; if it does
not, remove the wrapper entries so the refusal names the model class.

**Acceptance:** A test pickles an `XGBRegressor` and an `LGBMRegressor` into a
project folder and loads each through `safe_unpickle`: both load and predict,
or both are refused naming their own class; the Load File page matches.

**Dependencies:** None.

**Evidence:** `src/haute/_sandbox.py` (`_ALLOWED_PICKLE_CLASSES`,
`safe_unpickle`); `docs/building-models/nodes/external-file.md`.

### BUG-05 — The Databricks query field says what it accepts
**Why:** For a Databricks Data Input, `query` is only a SELECT clause: Haute
appends `FROM <table>`, and a query with `FROM`, a semicolon, a comment or a
write keyword is refused. The editor's hint calls the field an "Optional
projection/filter clause", but a filter (`WHERE`) cannot work there, because
it would come before the `FROM` Haute appends.

**Plan:** Reword the hint to say the field takes a `SELECT` list of columns and
Haute adds `FROM` and the table.

**Acceptance:** The hint names a SELECT clause without FROM; the Data Input
editor's test pins the wording.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/DataInputEditor.tsx` (the
Databricks query hint); `src/haute/_databricks_io.py` (the SELECT validation);
`docs/building-models/nodes/data-input.md`.

### BUG-06 — A Delta table folder can be chosen as a Data Input
**Why:** A Delta table is read from its folder. The Data Input's path browser
opens a folder when it is clicked and only ever selects a file, and an input
has no manual path entry (only outputs get one), so a Delta Lakehouse input's
**TABLE LOCATOR** cannot be set in the editor; only a hand-edited pipeline file
reaches it. Iceberg is not affected: `scan_iceberg` reads a table from its
metadata file (`metadata/<version>.metadata.json`), and for a format without
extensions the browser lists files with any installed file format's extension,
`.json` among them, so it can select that file.

**Plan:** Let the browser select a folder for the Delta format (a Delta folder
is recognisable by its `_delta_log`), or give lakehouse inputs the same manual
path entry outputs have.

**Acceptance:** In the editor, a Lakehouse Data Input can be pointed at a Delta
table folder under the project and previews it; a frontend test covers the
selection.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/_IoFormatEditor.tsx`
(`manualEntry={direction === "output"}`); `frontend/src/panels/editors/_shared.tsx`
(the browser's folder click); `src/haute/routes/files.py` (directory items);
`src/haute/_polars_io_registry.py` (the `delta` and `iceberg` formats).

### BUG-07 — The Load File picker offers only loadable files
**Why:** The Load File path browser lists `.onnx` and `.pmml` files, but no
File Type loads them (Pickle, JSON, Joblib and CatBoost are the only loaders),
so choosing one produces a node that cannot run.

**Plan:** Drop the two extensions from the picker, or add loaders for them if
Load File is meant to support those formats.

**Acceptance:** The picker's extensions and the File Type loaders agree; a
frontend test pins the extension list.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/ExternalFileEditor.tsx` (the
picker's `extensions`); `src/haute/_io.py::_load_external_object_uncached`.

### BUG-08 — A table added by hand starts with a valid label
**Why:** A Quote Input table's label becomes its frame name downstream and
must be an identifier. **Add Table** gives a new table its path as its label
(`$[:]` for the first), which fails that rule, so every hand-built table
starts invalid until the analyst renames it.

**Plan:** Derive the new table's label the way inference does (`quote_info`
for the root, the array's key below it), de-duplicated against existing
labels.

**Acceptance:** A table added with **Add Table** has a label that passes the
label rule; a frontend test covers the root and a nested table.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/ApiInputEditor.tsx` (`addTable`);
`specs/json-shredding/high-level.md` (the label rule).

### BUG-10 — A CSV's detected schema uses the node's reader arguments
**Why:** A CSV Data Input detects its columns through `GET /api/schema`, which
receives only the path and reads the file with the CSV reader's defaults; the
node's **ARGUMENTS** (a `separator`, a quote character, `has_header`) are never
sent. For a file that needs one, the detected columns are wrong: a `;` file
with the header `claim_id;amount` is detected as the single column
`claim_id;amount`. **Use detected schema** then writes that column into
`arguments.schema`, and the node's next read fails with Polars' `SchemaError`
"provided schema does not match number of columns in file (1 != 2 in file)".
The Data Input page tells analysts to leave the button alone for such a file.

**Plan:** Detect a file input's schema from the node's own reader settings
(format and arguments, the `schema` argument aside) through the same reader the
snapshot uses, instead of from the path alone.

**Acceptance:** A CSV Data Input with `separator: ";"` detects its columns split
on `;`, and **Use detected schema** writes those columns; a backend test on the
schema request covers the arguments, a frontend test that the editor sends
them; the Data Input page drops its warning.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/DataInputEditor.tsx` (`useSchemaFetch`
is given only the path); `src/haute/routes/files.py::get_schema`
(`graph_utils.read_source` on the path); `docs/building-models/nodes/data-input.md`.

### BUG-11 — Setting every Source Switch input to `-` restores passthrough
**Why:** Choosing `-` for an input stores it in `input_scenario_map` with an
empty string instead of removing it. `select_live_switch_input` treats a
non-empty map as exhaustive and passes through the first input only when the
map is empty, so once an input has been mapped, setting every input back to
`-` leaves a map of empty strings and the node fails for every source with
`LiveSwitchScenarioError`, where a new switch would pass its first input
through. The Source Switch page documents this.

**Plan:** Make `-` delete the input's key, so a switch whose inputs are all on
`-` has an empty map and behaves as a new one.

**Acceptance:** A frontend test maps an input, sets it back to `-`, and the
committed config has no key for it; the Source Switch page drops its note.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/editors/LiveSwitchEditor.tsx` (`setMapping`);
`src/haute/_node_apply.py::select_live_switch_input`;
`docs/building-models/nodes/source-switch.md`.

### BUG-12 — A save keeps the statements written after the pipeline constructor
**Why:** Codegen regenerates a pipeline file from the parsed graph, and the
parser keeps only the preamble (the code between the imports and the
constructor), the node functions, the `connect` chains, submodel registrations
and `# haute:preserve-start` / `# haute:preserve-end` blocks. Every other
module-level statement after the constructor (a constant, a helper function,
trailing code) is neither preamble nor a preserved block. The file loads as
ready with no diagnostic, and the next save regenerates it without those
statements while keeping the node code that uses them, so the saved pipeline
raises `NameError` when it runs. Reproduced on 2 October 2026: a file with
`RATE = 1.05` after the constructor, `THRESHOLD = 10` and `def helper(x)`
between two nodes, and a statement after the `connect` calls regenerated
without any of the four, while a node body still called `helper(THRESHOLD)`.

**Plan:** The parser records each such statement, with its line span, as a load
diagnostic that the editor document carries and the canvas shows. Save refuses
while the file on disk holds one, naming each line and the two fixes: move the
statement above the constructor, where it becomes preamble, or wrap it in
preserve markers. The file is never rewritten without the statement. The
statements that stay recognised are the node functions, `connect` chains,
submodel registrations, preserved blocks and the generated `global_constants`
binding.

**Acceptance:** A pipeline with a constant, a helper function and a trailing
statement after the constructor loads with one diagnostic per statement, naming
its line; save refuses with those lines and fixes and leaves the file
byte-identical; and the same statements above the constructor or inside
preserve markers save and regenerate unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/codegen.py::_render_module`;
`src/haute/_ast_helpers.py::_extract_preamble_from_ast`;
`src/haute/_ast_helpers.py::_extract_preserved_blocks`;
`src/haute/parser.py::parse_pipeline_source`.

### BUG-13 — Deployed External File code sees the preamble, as in the editor
**Why:** In the editor an External File's code runs with the preamble's names
and `obj` (`_build_external_file` copies the preamble namespace and adds the
loaded object), and in a standalone run its hook is a module function that sees
the module's globals. The deploy scorer's External File intercept, which every
bundled External File with code runs through because its artifact path is
remapped into the bundle, runs the code with `obj` alone. Code that calls a
preamble helper or a module the preamble imports (`np`, a `utility` function)
previews in the editor and raises `NameError` in the deployed scorer, against
the rule that the same pipeline runs the same way in every context.

**Plan:** Run the intercepted External File code with the preamble namespace
the intercept already receives in its build arguments, plus `obj`, exactly as
the editor builder does.

**Acceptance:** A deploy scorer test serves an External File whose code calls a
preamble helper and returns the editor preview's result.

**Dependencies:** None. `GCONST-03` binds `global_constants` in the same
namespace.

**Evidence:** `src/haute/deploy/_scorer.py::_intercept`;
`src/haute/_builders.py::_build_external_file`;
`src/haute/_user_exec.py::_exec_user_code`.

### BUG-14 — Model Score code sees the preamble on the canvas and when deployed
**Why:** The pipeline-config specification says canvas execution gives a hook's
code the names its saved function has, with `model` as the one difference. A
Model Score's code runs with `df`, its other inputs and `model` only
(`_run_score_pipeline` passes no preamble namespace), so it never sees the
preamble's names on the canvas or in the deployed scorer, which builds the node
the same way. In a standalone run its hook is a module function that sees them.
Code that calls a preamble helper therefore runs standalone and raises
`NameError` on the canvas and when served. Every other code box (Transform,
Data Input, External File, Rating Step, Scenario Expander and Explore) gets the
preamble's names on the canvas.

**Plan:** Pass the build context's preamble namespace through the Model Score
builder to the code execution, with `model` beside it, and state the names in
the specification's hook paragraph.

**Acceptance:** An executor test runs Model Score code that calls a preamble
helper and gets the standalone run's result, and a deploy scorer test serves
the same node.

**Dependencies:** None. `GCONST-03` binds `global_constants` in the same
namespace.

**Evidence:** `src/haute/_model_scorer.py::_run_score_pipeline`;
`src/haute/_builders.py::_build_model_score`;
`specs/pipeline-config/high-level.md` (declarations and hooks).

### BUG-15 — A bare `pipeline.run()` routes a source the pipeline has
**Why:** `Pipeline.run()` sets the scenario to `"batch"`, while `haute run`
executes `live` and the editor executes the toolbar's source. No pipeline has a
source called `batch` unless the analyst adds one, so a Source Switch mapped to
the pipeline's own sources fails a standalone run. Reproduced on 2 October 2026
with a two-input switch mapped to `live` and `nb_batch`: `pipeline.run()` raised
`LiveSwitchScenarioError` ("Live switch 'switch' has no input for scenario
'batch'"). The scenario also selects batched model scoring (anything but
`live`), so the default cannot simply become `live` without changing how a
standalone run scores models.

**Plan:** Decide between making `run()` require a keyword `source` and refuse to
guess, or defaulting to the pipeline's only non-live source when there is
exactly one and requiring `source` otherwise. Then implement it, update the
docstring and the pipeline-config specification, and fix the documentation's
`pipeline.run()` examples.

**Acceptance:** `pipeline.run(source="nb_batch")` routes the switch's
`nb_batch` input; a bare `run()` behaves as decided, with any refusal naming the
sources; and `tests/test_pipeline.py` covers both.

**Dependencies:** None. `GCONST-03` gives `run()` the same `source` keyword with
today's default, so whichever lands second keeps one keyword and this package
owns the default.

**Evidence:** `src/haute/pipeline.py::Pipeline.run`;
`src/haute/_model_scorer.py::_scenario_ctx`;
`src/haute/_standalone_nodes.py::run_configured_node`.

### BUG-16 — Optimiser requests carry the preamble
**Why:** Every other execution request the editor sends (previews, training,
Explore, Data Output and Output) builds its graph with the preamble, but the
optimiser's estimate and auto-range requests use a graph builder in
`OptimiserConfig.tsx` that passes nodes, edges and submodels only, and the
solve request in `solveActions.ts` does the same. The optimiser service compiles
`body.graph.preamble or ""`, so it runs with no preamble at all: a Transform
upstream of an Optimisation node that calls a preamble helper, or uses a module
the preamble imports, previews correctly and then raises `NameError` in the
optimiser's estimate, auto-range and solve.

**Plan:** Build the optimiser's graphs with the preamble like every other
request. `GCONST-04` makes both graph builders read the graph store's constants
when called; reading the preamble there too closes this class of omission for
every caller.

**Acceptance:** Request tests prove the estimate, auto-range and solve requests
carry the preamble, and a backend test solves a pipeline whose Transform calls a
preamble helper.

**Dependencies:** None.

**Evidence:** `frontend/src/panels/OptimiserConfig.tsx` (`buildGraphCb`);
`frontend/src/panels/optimiser/solveActions.ts`;
`frontend/src/panels/optimiser/useOptimiserAutoRange.ts`;
`src/haute/routes/_optimiser_service.py`.

### BUG-17 — A standalone run of a pipeline with a submodel says what it cannot do
**Why:** `Pipeline.submodel()` records an occurrence without loading its
definition, and `run()` and `score()` sort and execute the root registry only.
A standalone run of any pipeline that wires a submodel therefore fails before
any node runs, with a message that contradicts the registration: reproduced on
2 October 2026, a pipeline registering `pipeline.submodel("modules/rating.py",
"rating")` and connecting nodes to it raised `UnknownEdgeEndpointError`
("Edges reference unknown node IDs: rating"), whether the submodel fed a later
node or was terminal. `haute run` and the editor execute the same pipeline,
because they parse and flatten it.

**Plan:** Decide between executing submodel occurrences in standalone runs (load
each definition through its `pipeline_dir`, register its nodes under qualified
names and wire its ports as flattening does) and refusing such a run up front
with a message that names the submodel and points to `haute run`. Then
implement it and state it in the pipeline-config specification.

**Acceptance:** A standalone run of a pipeline with a submodel either returns
`haute run`'s result or raises the decided message naming the submodel, in
`tests/test_pipeline.py`.

**Dependencies:** None. `GCONST-03`'s standalone parity covers root nodes until
this lands.

**Evidence:** `src/haute/pipeline.py::Pipeline.submodel`;
`src/haute/pipeline.py::Pipeline.run`;
`src/haute/pipeline.py::Pipeline._topo_order`.
