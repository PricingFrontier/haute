# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. These packages came from the 27 September 2026 audit of the
Getting Started and Building Models documentation, which checked every page
against the code, and from the review of that documentation on 28 September
(`BUG-10`, `BUG-11`): each one is a place where the code, not only the page, is
wrong. `BUG-18` was found on 4 October 2026 while fixing `BUG-17`. `BUG-12` came from reading the parser, the code generator,
the executors, the deploy scorer and the editor's request builders for the
[global constants](../pipeline-config/high-level.md#behaviour) specification and its review on
2 October 2026.
Current rating behaviour is specified in
[the rating specification](../rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-12 | Planned | P1 | A save never drops a statement written after the pipeline constructor; such a statement is reported and saving waits for it to move. |
| BUG-18 | Decision | P1 | A deployed pipeline runs each submodel, and an unflattened submodel placeholder never reaches an executor silently. |

## Planned improvements

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

### BUG-18 — A deployed pipeline runs its submodels
**Why:** `parse_pipeline_file` keeps submodel occurrences as placeholder nodes
unless it is asked to flatten them, and the executor's builder for a
placeholder (`_build_submodel`) passes its input through. The editor's routes
flatten every graph they execute, and `haute run` now does too, but deploy
parses the pipeline without flattening (`deploy/_config.py`), prunes and
validates that graph, and scores it with the placeholder still in place. A
deployed pipeline with a submodel therefore appears to skip it: the node after
the occurrence receives the frame that went into it, and the price is wrong
with no error when the columns happen to line up. `haute run` showed the same
failure on 4 October 2026 (the example project
`tests/assistant_eval/projects/submodel_pricing` failed with "unable to find
column vehicle_factor") before it was made to flatten; deploy's behaviour is
from reading the code and has no reproduction yet.

**Plan:** Decide whether deploy flattens right after parsing or later (the
pruner reads `graph.submodels`, and bundled artifact keys are built from node
ids, which flattening turns into `submodel_runtime/<occurrence>/<node>`). Then
flatten for deploy, and make the placeholder builders refuse to build, naming
the occurrence, so an unflattened graph fails instead of passing data through;
check first that no other caller of `parse_pipeline_file` (`_scaffold.py`,
`_pipeline_repair.py`, `routes/_helpers.py`) reaches a builder with one.

**Acceptance:** A deploy scorer test serves a pipeline whose output sits behind
a submodel and returns the submodel's columns; executing a graph that still
holds a placeholder raises an error naming the occurrence.

**Dependencies:** None.

**Evidence:** `src/haute/deploy/_config.py` (`parse_pipeline_file`);
`src/haute/deploy/_pruner.py::prune_for_deploy`;
`src/haute/_builders.py::_build_submodel`; `src/haute/parser.py::parse_pipeline_file`
(`flatten=False` by default); `src/haute/cli/_run.py`.
