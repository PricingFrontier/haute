# Name collisions roadmap

## Scope

Every place a name a user chooses becomes a Python binding, a parameter, a
column, a file or a storage key, and two of them can collide. The node naming
rule is specified in
[the codegen specification](../codegen/high-level.md) ("One function per
node"). Exact and sanitized duplicate node labels are already refused at save
(`routes/_save_pipeline.py::_validate_executable_names`), at codegen
(`codegen.check_executable_names`), at parse (exact duplicates in one
file, `_graph_builders.py`) and by the standalone `Pipeline` class
(`pipeline.py::_register_node`). These packages cover the collisions nothing
checks, the entry points that check less than save does, the editor
feedback that arrives only as a failed save, and the keys inside one node's
configuration that silently keep the last value.

Decided on 3 October 2026: a generated column (a Model Score output or
probability column, a rating table or combined output, a banding output, a
Scenario Expander column) may replace a same-named column already in the
frame. That is intended behaviour, stated in the rating and
mlflow-model-registry specifications, and is not a collision these packages
refuse. Node names that shadow a Python built-in or differ from another only
in case are refused (`NAME-01`).

Inventory made on 3 October 2026 at `main` `10139e5dc`, then reviewed by
Codex. Reproduced at that commit:
- importing a pipeline file with a node labelled `pl` rebinds `pl` to the
  node, so every later `pl.col(...)` in the file fails;
- a node labelled `pipeline` makes the file fail to import with
  `AttributeError: 'function' object has no attribute 'output'`;
- a Constant node with two entries named `rate` yields one column holding
  the second value (`_node_apply.constant_frame` builds a dict).

The canvas runs the first two pipelines, because node bodies execute against
the preamble namespace and never see the other node functions
(`_user_exec.py`).

"Reserved names" below always means the names the generated module binds
itself: `haute`, `pl`, `pipeline`, `submodel` and `global_constants`, plus
`df` for input names. Comparison with them is exact, as Python's is.
Comparison between two node names ignores case.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| NAME-05 | Delivered | P1 | Duplicate keys inside one node's config are refused instead of keeping the last. |
| NAME-01 | Delivered | P2 | One executable-name rule, with reserved names, applied at every entry point. |
| NAME-02 | Delivered | P2 | A loaded file with colliding names says so and stays renameable. |
| NAME-03 | Delivered | P2 | Node and input names cannot collide with support code, and support code cannot collide with itself. |
| NAME-04 | Delivered | P2 | The editor allocates free names on create and refuses colliding renames inline. |
| NAME-07 | Delivered | P3 | Utility file names are importable and portable, and the panel shows refusals. |
| NAME-08 | Delivered | P3 | Deploy artifact keys and Data Output destinations cannot collide. |

## Delivery notes

Delivered on 3 October 2026 on the `duplicate-names` branch, one commit per package. Two
departures from the plans below:
- `df` is not among the reserved input names of `NAME-01`: it keeps its existing rule (an input
  of that name is refused where node code reads it and is an ordinary input on a node type
  without code), which the codegen and pipeline-config specifications already state, and the
  API Input editor does not reserve it.
- `NAME-04`'s editors that set an input binding without creating or renaming a node (API frame
  labels, `inputMapping` aliases, submodel ports) do not yet ask the identity request for the
  edited binding's violations. Such a binding is still refused at save and by the strict parse,
  with the `NAME-01`/`NAME-03` message, and a reserved frame label is refused inline.

## Planned improvements

`NAME-05` threatens computed results and is independent of the rest.
Deliver `NAME-01` before `NAME-02`,
`NAME-03` and `NAME-04`, which call the rule it introduces, and `NAME-02`
before `NAME-04`, which builds on its extended identity request. `NAME-07` and
`NAME-08` are independent.

These packages make some files that save today invalid. Haute has no
installed base to migrate; such a file loads with `NAME-02`'s diagnostics and
is fixed by renaming.

### NAME-01 — One executable-name rule, with reserved names
**Why:** The duplicate check is written three times (save, codegen, parse)
with different reach, and none of them reserves the names the generated
module binds itself (`codegen._render_module` emits `import haute`,
`import polars as pl`, the preamble, `pipeline = haute.Pipeline(...)`,
`global_constants = pipeline.global_constants`, then one `def` per node). A
node whose function name is reserved rebinds that name for every later node
body when the file is imported or run with `pipeline.run()`, while the canvas
keeps working. Only `global_constants` is refused today
(`_save_pipeline.py::_validate_global_constants`). Input names have the same
gap: `_user_exec.py` binds inputs over `{"pl": pl}`, so a Quote Input table
label, a submodel port, or an `inputMapping` alias named `pl` replaces polars
inside the consuming body (`inputMapping` replaces the edge-derived name,
`_graph_utils.py::resolve_input_mapping_names`, so `{"pl": "claims"}` binds `pl`
although the source is named `claims`), and one named `global_constants`
replaces the constants view. Only `df` is refused. A node named after a
Python built-in (`max`, `filter`, `id`, `ValueError`) breaks later node bodies
in the same standalone way. Node names that differ only in case (`Claims`
and `claims`) read as one name in the editor, traces and messages; config
sidecars, API parquet stems and submodel files already compare such names
ignoring case, while code-only nodes do not.

**Plan:** Add one module, `haute._executable_names`, that owns:
- the reserved names;
- the rule for node function names and submodel occurrence aliases: unique
  across the root graph and every submodel graph, compared with
  `str.casefold` (so `Claims` and `claims` collide), never reserved, and
  never a public name of `builtins`. The built-in rule affects only labels
  that sanitize to exactly such a name: lower-case functions and types
  (`max`, `filter`, `id`) and the capitalised exception and constant names
  (`Exception`, `ValueError`, `Ellipsis`, `NotImplemented`); `Max premium`
  and `Filter` stay valid;
- the rule for effective input bindings: every name a node body receives, as
  the executor binds it after `inputMapping` and instance mapping are
  applied (sanitized source label, API frame label, submodel port name,
  mapped alias), is not reserved;
- one result type listing each collision bucket or reserved hit with the
  node ids, labels and the module each sits in, and one message format.

Route the save validator, `codegen.check_executable_names` (then `check_function_name_collisions`), the
parser's duplicate check, the assistant's add and rename checks
(`assistant/_ops.py`) and `Pipeline._register_node` through it, and delete
the duplicated loops. In `_register_node`, refuse a reserved name, and
refuse a function whose name is already bound in `f.__globals__` to an
object other than `f` itself; that keeps the direct form
`pipeline.polars(existing_function)` working. Serve the reserved input names
in `reserved_api_input_frame_labels` (today `keyword.kwlist` only,
`_pipeline_recovery.py::_capabilities`) so the API Input editor's existing
inline check refuses them. Restate the reason for project-wide uniqueness in
the codegen specification and in the comments in `_save_pipeline.py` and
`codegen.py`: it is a deliberate policy (one name means one node in labels,
traces, messages and generated files), not a consequence of bare-name
flattening, which `_submodel_instances.expand_submodel_instances` no longer
does (children are re-identified by qualified runtime id).

**Specification:** the codegen high- and low-level naming rule and failure
table; the expression-parsing failure table; the API Input reserved-label
contract in `frontend-graph-canvas`.

**Acceptance:**
- A node labelled `pl`, `pipeline`, `submodel`, `haute` or
  `global_constants` is refused at save, at codegen, by a strict parse
  (`haute run`, deploy) and by a standalone import, each with a message
  naming the node; the editor load reports it (`NAME-02`).
- A Quote Input table, a submodel port and an `inputMapping` alias named
  `pl` or `global_constants` are each refused at save and at execution.
- Nodes labelled `max` and `ValueError` are refused at save, by a strict
  parse and by a standalone import; `Max premium` is accepted.
- Nodes labelled `Claims` and `claims` are refused at save and reported by
  the editor load.
- `pipeline.polars(f)` on an already-defined `f` registers; a decorated
  function whose name a preamble helper already binds is refused.
- The existing duplicate-label tests pass, apart from any that expect
  case-only labels to coexist, which are updated.
- One table-driven test covers the rule; each entry point has one test
  proving a violation reaches the user through it.

**Dependencies:** None.

**Evidence:** `src/haute/_graph_utils.py::_sanitize_func_name`,
`executable_input_name`; `src/haute/codegen.py::_render_module`,
`check_executable_names`;
`src/haute/routes/_save_pipeline.py::_validate_executable_names`,
`_validate_global_constants`; `src/haute/_graph_builders.py` duplicate
function-name check; `src/haute/pipeline.py::_register_node`;
`src/haute/_user_exec.py` input binding; `src/haute/assistant/_ops.py` add
and rename checks; `src/haute/_pipeline_recovery.py::_capabilities`.

### NAME-02 — A loaded file with colliding names says so and stays renameable
**Why:** Parse checks only exact duplicates within one file and an
occurrence alias against root node ids (`_parser_submodels.py`). A
hand-edited project where a root node shares a name with a submodel child,
two definitions share a child name, or an alias matches a child loads as
ready with no complaint, and then every save fails with a 400 the user did
not cause in this session. Routing such a file into recovery would not help:
recovery documents cannot be mutated or saved
(`_pipeline_recovery.py::_capabilities` grants `can_mutate` and `can_save`
only when ready), so the user could not rename anything.

**Plan:** Split the parser's name checks into two kinds.
- *Structural:* a collision of canonical graph identities within one graph
  stays a parse error, because the colliding entries would collapse into one
  node id: two functions with the same name in one file
  (`_graph_builders.py`), two submodel occurrences with the same name, and an
  occurrence sharing a root node's name (occurrence names become graph ids,
  `_parser_submodels.py`). Such a file goes to recovery as today.
- *Semantic:* every other `NAME-01` violation (and `NAME-03`'s, once it
  exists) is collected after the graph is built, without discarding the
  graph. The one parser returns the graph with its violations. Strict callers
  (`haute run`, deploy, codegen's post-save parse) raise on any violation,
  with the `NAME-01` message.

The editor document loader (`_pipeline_recovery.py`, which calls the strict
parser today) reads the violations into a new `name_violations` list on
`PipelineEditorDocument`, beside the existing `completeness` list. The
document stays `ready` with `can_mutate`; while `name_violations` is
non-empty, `can_save`, `can_execute` and `can_preview` are false and the
server refuses save and execution with the `NAME-01` message, so the canvas
never runs a pipeline its own file cannot run. The editor shows the
violations in a persistent banner that lists each one and selects its nodes
on click. Renames use the ordinary rename path. After each rename the
browser revalidates the document through `POST
/api/pipeline/editor-identities`, which this package extends with the
document's naming context and a `violations` response (the request and the
utility-file read are specified under `NAME-04`, which adds allocation on
top); the banner shrinks as violations are fixed. When the list is empty,
save and execution are available again.

**Specification:** the expression-parsing failure table (structural and
semantic name checks); the `PipelineEditorDocument` and capabilities
contract in `server-api`; the document load banner in `frontend-shared`.

**Acceptance:**
- In an editor integration test, a project with two violations (a root node
  sharing a name with a submodel child, and a node labelled `pl`) loads
  editable with a banner naming both, and preview and save are refused.
  Renaming the first leaves one entry; renaming the second clears the
  banner; the save then succeeds and preview runs.
- `haute run` and deploy on the original file exit non-zero with the
  message.
- A file with two functions of the same name, one with two occurrences of
  the same name, and one with an occurrence named like a root node each
  still load into recovery, with no node collapsed into another.

**Dependencies:** `NAME-01`. `NAME-02` and `NAME-04` share the extended
identity request; `NAME-02` delivers the naming context and the
`violations` response, and `NAME-04` adds allocation and per-candidate
collisions.

**Evidence:** `src/haute/_parser_submodels.py`;
`src/haute/_pipeline_recovery.py::_capabilities`;
`src/haute/_submodel_instances.py`.

### NAME-03 — Names cannot collide with support code
**Why:** Support code binds names at module level before the node
functions: the preamble (with `from utility.<module> import *`), and the
preserved blocks codegen keeps after the constructor
(`codegen._render_module`). A node with the same function name as a helper
rebinds it, so another node that calls the helper calls the node's runner
when the file runs standalone, while the canvas calls the helper. Nothing
checks the reverse either: a utility file, saved on its own route, can add a
helper with an existing node's name. Support code can also collide with
itself: a submodel's preamble is appended to the parent's when instances are
expanded (`_submodel_instances.py`), so a root and a submodel preamble that
define the same helper differently leave every node calling whichever came
last, and two star-imported utility modules that bind one name differently
resolve to the last import. Support code can rebind a reserved name too, for
example a utility module that defines `pipeline`.

**Plan:** Compute a support-code binding inventory statically, with
provenance (where each binding is made and what it binds), from the root
preamble, each submodel preamble and the preserved blocks. Supported forms:
`import` and `from … import` (with aliases), top-level `def`, `class` and
assignment targets, and a star import of `utility.<module>`, resolved by
parsing that file under the same supported forms, recursively for its own
`utility.*` star imports with cycle detection. A utility module's exports are
its literal `__all__` (a list or tuple of string literals) or, without one,
its top-level names without a leading underscore. Anything else that makes
the inventory incomplete is refused with a message naming the statement: a
computed `__all__`, a definition under `if`/`try` at module level in a
star-imported utility, or a star import of a module outside `utility`
(nothing outside the project is imported to check a name). Then refuse:
- a node function name or effective input binding (`NAME-01`) that equals a
  support-code binding;
- one name bound by two support-code sources to different provenance
  (re-importing the same object, such as `import polars as pl` in two
  places, is the same provenance and is accepted);
- a support-code binding of a reserved name, apart from the canonical
  imports `import haute` and `import polars as pl`.

Apply the check at save, at load (`NAME-02`), in the editor identity check
(`NAME-04`), at execution start for `haute run` and deploy, and in the
utility save route, which checks each project pipeline whose support code
star-imports the saved module and refuses naming the pipeline and the
colliding node.

**Specification:** the codegen naming rule and preamble contract; the
utility routes in `server-api`.

**Acceptance:**
- A preamble `def rate_lookup` with a node labelled `rate_lookup` is refused
  at save and by a strict parse, and reported by the editor load; so is a
  Quote Input table labelled `rate_lookup`.
- Saving a utility file that adds `rate_lookup` while a pipeline that
  imports it has that node is refused, naming the pipeline and the node.
- Two utility modules defining different `band` functions, both
  star-imported, are refused; a root and a submodel preamble defining
  different `band` functions are refused.
- A utility module and the preamble that both `import polars as pl` are
  accepted.
- `from math import *` in the preamble, and a utility module with a computed
  `__all__`, are refused with messages naming the statement.

**Dependencies:** `NAME-01`.

**Evidence:** `src/haute/codegen.py::_render_module` (preamble and preserved
blocks); `src/haute/_submodel_instances.py` (child preamble appended to the
parent's); `src/haute/executor.py::_compile_preamble`;
`src/haute/routes/utility.py`; `src/haute/_user_exec.py`.

### NAME-04 — The editor allocates free names and refuses colliding renames
**Why:** The browser never checks node labels against each other. The
editor identity request sends only the nodes being created or renamed
(`utils/editorIdentities.ts`), so the server cannot see the rest of the
graph. Duplicate and paste both label the copy `"<label> copy"`, so copying
twice gives two identical labels; paste copies a submodel occurrence's alias
verbatim; a default label such as `Transform 7` can repeat a label the user
gave another node. Each surfaces later as the save toast "Failed to save
pipeline: …" with no link to a node. The node panel's header rename skips
`RenameDialog`'s checks, and `SubmodelDialog` closes on submit, so a refused
name is lost.

**Plan:** Extend `POST /api/pipeline/editor-identities` (the naming context
and `violations` response arrive with `NAME-02`; this package adds
allocation and per-candidate collisions). It stops being pure: its verdict is a function of the request and of the project's saved
utility files, which it reads (and only those) to resolve
`utility.<module>` star imports. Utility files reach the disk only through
the utility save route, and save, preview and the deployed scorer read the
same files, so the editor and save always judge one set of utility
contents; a utility edit changes the next request's verdict without any
browser-side cache.
- The request adds the editor document's naming context in the
  representation save receives: every node's id, label, type and function
  name, with the config fields that make bindings (`inputMapping`,
  `instanceOf`, the occurrence alias, API frame labels, submodel ports);
  every edge with its canonical source and target handles; each submodel
  definition's child graph; the root and submodel preamble text; and the
  preserved blocks. It also adds a per-request flag `allocate`. From it the server
  builds the same graph, effective input bindings and support-code
  inventory that save builds, through the same `NAME-01` and `NAME-03`
  functions.
- With `allocate`, the server assigns each candidate, in request order, the
  first free name that passes the `NAME-01` and `NAME-03` rules, counting
  names already assigned to earlier candidates in the same request. An
  ordinary node's label gets `" 2"`, `" 3"`, … appended; a submodel
  occurrence's alias, which must stay a canonical identifier, gets `_2`,
  `_3`, ….
- The response adds, per node, the resolved `label` and, for an occurrence,
  the resolved `alias`, beside the existing identities. Without `allocate`, a
  node that breaks a rule gets a collision result naming the other party
  instead of identities. The response also carries the document's remaining
  violations after the request is applied, which `NAME-02`'s banner shows.
- Editors that set an input binding without creating or renaming a node
  (API frame labels, `inputMapping` aliases, submodel ports) send the same
  request with no candidate nodes and show the violations it returns for
  the edited binding.
- Palette drop, edge-drop, duplicate, paste and Create Instance send
  `allocate`; rename does not. The browser applies a batch's results
  together or not at all, behind the existing stale-request fences.
- Both rename surfaces share one client validator and show the server's
  collision inline; nothing is applied on refusal. `SubmodelDialog` stays
  open and shows a refusal inline with the typed name.

**Specification:** `frontend-graph-canvas` node creation and rename;
`server-api` editor identities (the request, the response and the purity
statement).

**Acceptance:**
- Duplicating a node twice gives `X copy` and `X copy 2`; pasting two copies
  of one node in one paste gives two distinct labels; pasting a submodel
  occurrence `rates` gives `rates_2`.
- Renaming a node to another node's label, to `pl`, or to a preamble
  helper's name is refused inline in both the rename dialog and the node
  panel, and the graph is unchanged.
- A graph change while an allocation is in flight applies nothing, as the
  existing fence tests prove for identities.
- A refused submodel name keeps the dialog open with the typed name.
- With the preamble `from utility.rates import *` unchanged, saving
  `utility/rates.py` with a new `def band` turns a rename to `band` from
  accepted to refused.
- One shared test fixture of graphs gets the same verdict from the identity
  endpoint and from save; it includes an ordinary instance (`instanceOf`)
  whose `inputMapping` maps an input to `pl`.

**Dependencies:** `NAME-01`, `NAME-02`; `NAME-03` for the preamble case.

**Evidence:** `frontend/src/utils/editorIdentities.ts`;
`frontend/src/hooks/useNodeHandlers.ts` (duplicate, Create Instance);
`frontend/src/hooks/useKeyboardShortcuts.ts` (paste);
`frontend/src/utils/flowElements.ts::nodeLabel`;
`frontend/src/hooks/useGraphCommitController.ts::onRenameNode`;
`frontend/src/components/RenameDialog.tsx`; `frontend/src/panels/NodePanel.tsx`;
`frontend/src/components/SubmodelDialog.tsx`;
`src/haute/_editor_identities.py`; `src/haute/routes/pipeline.py`;
`src/haute/pipeline.py` (occurrence names must be canonical identifiers).

### NAME-05 — Duplicate keys inside one node's config are refused
**Why:** Two node configs keep the last of two same-named entries without a
word. A Banding node applies its factors in order with
`with_columns(... .alias(output_column))` (`_rating.py`), and nothing checks
that two active factors name different output columns, so the later band
replaces the earlier one. A Constant node builds its frame from a dict
(`_node_apply.constant_frame`, also called by standalone execution in
`_standalone_nodes.py`), so two entries named `rate` produce one `rate`
column with the second value; recovery inspection reports the duplicate, but
execution does not. The Constant editor also offers `constant_<n+1>` as a new
name, which repeats an existing one after a deletion. Rating tables already
refuse duplicate output columns in both the engine and the editor.

**Plan:** Refuse duplicate active banding `outputColumn` values in the
banding config validation (`_banding_config.py`), and duplicate non-empty
Constant names in one shared validation that `constant_frame`'s callers
reach, in the executor and standalone alike. Show both refusals inline in
`BandingEditor` and `ConstantEditor`, as `RatingStepEditor` does for rating
outputs, and make the Constant editor offer the first free `constant_<n>`.

**Specification:** the rating specification's banding config rules; the
Constant node contract in `pipeline-config`; `frontend-node-editors` Banding
and Constant editors.

**Acceptance:**
- A Banding node with two active factors writing `age_band` fails at
  execution with a message naming the column and both factors; a draft
  factor sharing the name does not count; the editor marks both factors and
  blocks the edit.
- A Constant node with two `rate` entries fails in the executor and in a
  standalone run with a message naming `rate`; the editor flags both rows.
- After deleting `constant_1` of two constants, adding one offers
  `constant_1`.

**Dependencies:** None.

**Evidence:** `src/haute/_rating.py` banding application;
`src/haute/_banding_config.py`; `src/haute/_node_apply.py::constant_frame`;
`src/haute/_standalone_nodes.py`; `src/haute/_node_config_recovery.py`;
`frontend/src/panels/editors/BandingEditor.tsx`;
`frontend/src/panels/editors/ConstantEditor.tsx`;
`frontend/src/panels/editors/rating/ratingTableUtils.ts` (the rating
precedent).

### NAME-07 — Utility file names are importable and portable
**Why:** The utility route accepts any ASCII identifier that does not start
with `__` (`routes/utility.py::_validate_module_name`), including hard
keywords such as `class`, whose `from utility.class import *` is a syntax
error in the preamble, and Windows device names such as `CON`, `NUL` or
`COM1`, which the repository already guards elsewhere
(`_config_io.is_windows_reserved_filename`). Its duplicate check is
`target.exists()`, which refuses `Features` beside `features` on Windows and
macOS but accepts it on Linux, where the checkout then breaks on the other
systems. In the utility panel a refusal is shown only while a module is
open, so it is invisible in the empty state and on the Imports view, and the
typed name is cleared before the request returns.

**Plan:** Refuse hard keywords and Windows device names (through
`is_windows_reserved_filename`), and compare the new name with existing
utility files using `str.casefold`. Show the panel's error in every state and
keep the typed name until the create succeeds.

**Specification:** the utility routes in `server-api`; the utility panel in
`frontend-node-editors`.

**Acceptance:** Creating `class`, `NUL` and `Features` beside `features` are
each refused with a message, on every platform (the tests do not depend on
the file system's case behaviour); the refusal is visible in the empty panel
with the typed name kept.

**Dependencies:** None.

**Evidence:** `src/haute/routes/utility.py`;
`src/haute/_config_io.py::is_windows_reserved_filename`;
`frontend/src/panels/UtilityPanel.tsx`.

### NAME-08 — Deploy artifact keys and Data Output destinations
**Why:** Deploy artifact keys are `f"{node_id}__{filename}"`
(`deploy/_bundler.py`). They are not injective (node `a` with `b__c.pkl` and
node `a__b` with `c.pkl` both give `a__b__c.pkl`), node ids that differ only
in case give keys that clobber each other on a case-insensitive file system,
and the keys are written into a plain dict, so the later artifact wins. Two
Data Output nodes can target one destination, and the second fails only when
it runs (`DataOutputDestinationExistsError`).

**Plan:** When the bundle is built, refuse two artifacts whose keys are
equal or equal ignoring case, naming both nodes and files. Define a Data
Output destination identity: a file target's resolved path (compared
ignoring case on Windows and macOS), and a database target's connection,
catalog, schema and table as the writer resolves them. A destination missing
a required part (a new Data Output starts with an empty path,
`node_defaults.json`, and save validates it with `require_complete=False`)
has no identity: it is not compared, it stays an editable, saveable draft,
and its completeness is required only when the output runs, as today.
Refuse two Data Output nodes, including those inside submodel occurrences
after expansion, with the same identity, in the same graph validation
`NAME-01` uses, so save, load, `haute run` and deploy all reach it.

**Specification:** `deploy` bundling; `io-layer` Data Output.

**Acceptance:** Artifacts `a`/`b__c.pkl` and `a__b`/`c.pkl` are refused
before upload; two Data Output nodes writing `out/result.parquet` are refused
at save and by `haute run` before any write; two writing the same table name
in different schemas are accepted; a pipeline with two unfinished Data
Output nodes (empty paths) saves and reloads unchanged.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_bundler.py`;
`src/haute/executor.py::resolve_data_output_path`,
`DataOutputDestinationExistsError`; `src/haute/node_defaults.json`;
`src/haute/routes/_save_pipeline.py` (`require_complete=False`).
