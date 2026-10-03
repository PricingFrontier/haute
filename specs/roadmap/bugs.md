# Bugs roadmap

## Scope

Defects found outside a component's own roadmap work, kept here until they
are fixed. These packages came from the 27 September 2026 audit of the
Getting Started and Building Models documentation, which checked every page
against the code, and from the review of that documentation on 28 September
(`BUG-10`, `BUG-11`): each one is a place where the code, not only the page, is
wrong. `BUG-12` came from reading the parser, the code generator,
the executors, the deploy scorer and the editor's request builders for the
[global constants](../pipeline-config/high-level.md#behaviour) specification and its review on
2 October 2026.
Current rating behaviour is specified in
[the rating specification](../rating/high-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| BUG-12 | Planned | P1 | A save never drops a statement written after the pipeline constructor; such a statement is reported and saving waits for it to move. |

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

