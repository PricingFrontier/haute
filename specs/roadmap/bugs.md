# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. These packages came from the 27 September 2026 audit of the
Getting Started and Building Models documentation, which checked every page
against the code, and from the review of that documentation on 28 September
(`BUG-10`, `BUG-11`): each one is a place where the code, not only the page, is
wrong. `BUG-12` to `BUG-17` came from reading the parser, the code generator,
the executors, the deploy scorer and the editor's request builders for the
[global constants](../pipeline-config/high-level.md#behaviour) specification and its review on
2 October 2026.
Current rating behaviour is specified in
[the rating specification](../rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-12 | Planned | P1 | A save never drops a statement written after the pipeline constructor; such a statement is reported and saving waits for it to move. |
| BUG-15 | Decision | P2 | A bare `pipeline.run()` routes a source the pipeline has instead of a `batch` scenario no pipeline declares. |
| BUG-17 | Decision | P3 | A standalone run of a pipeline with a submodel runs it, or refuses up front naming the submodel, instead of reporting it as an unknown node. |

## Planned improvements

`BUG-02` decides when `onMissing` matters for a table built in the editor, so
`BUG-01`'s control is easiest to place after it; the fix that stops the
setting being lost does not wait for it.

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

**Dependencies:** None. `run()` already takes a keyword-only `source`, for
global constants, with today's default `"batch"`; this package owns the
default.

**Evidence:** `src/haute/pipeline.py::Pipeline.run`;
`src/haute/_model_scorer.py::_scenario_ctx`;
`src/haute/_standalone_nodes.py::run_configured_node`.

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

**Dependencies:** None. Standalone global-constant parity covers pipelines
without submodels until this lands.

**Evidence:** `src/haute/pipeline.py::Pipeline.submodel`;
`src/haute/pipeline.py::Pipeline.run`;
`src/haute/pipeline.py::Pipeline._topo_order`.
