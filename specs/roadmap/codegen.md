# Pipeline parsing and code generation roadmap

## Scope

Reading a pipeline's `.py` file into a graph and writing a graph back as code,
as [the code generation specification](../codegen/high-level.md) and
[the expression parsing specification](../expression-parsing/high-level.md)
describe them: the constructor reader, the extraction of a node function's
body, the lift of hand-written Polars statements into steps, and the module
renderer. The packages below come from the 9 November 2026 codebase review.
Each was reproduced or measured against `HEAD` on that day by running the
named functions on a small pipeline source; the entry says which.

`CGEN-01` to `CGEN-03` are each one valid hand-written file the parser reads
wrongly or cannot read; they share no code and can be taken in any order.
`CGEN-04` is a cost, not a wrong result.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CGEN-01 | Planned | P2 | A pipeline's name and description are read however the constructor gives them, and a constructor the reader cannot read is refused instead of rewritten. |
| CGEN-02 | Planned | P3 | A decimal membership test with text that is not a number stays code; it no longer makes the whole file unreadable. |
| CGEN-03 | Planned | P3 | A closing `return` after a semicolon is rewritten correctly when the line holds non-ASCII text. |
| CGEN-04 | Planned | P3 | Code generation reads each node function's parameters once per save, not once per edge. |

## Planned improvements

### CGEN-01 — The constructor's name and description are read however they are given
**Why:** Reproduced. `Pipeline.__init__` takes `name` and `description` by
position or by keyword, but the reader takes the name from the first
positional argument only and the description from the `description=` keyword
only. `haute.Pipeline(name="motor", description="Motor pricing")` parses as a
pipeline called `main`, and the next save writes `haute.Pipeline("main", ...)`
over it; `haute.Pipeline("motor", "Motor pricing")` parses with no description
and the next save drops it. `haute.Submodel(...)` goes through the same reader
and falls back to `unnamed`. A constructor assigned with an annotation
(`pipeline: haute.Pipeline = haute.Pipeline("motor")`) is counted as the
constructor when the module's boundaries are found but is not read for its
name. Every other shape the parser cannot round-trip (a splat, a non-literal
argument) is refused; these are silently rewritten, and the pipeline's name is
what a deploy is named after.

**Plan:** Read the name and the description from either position or keyword
in one place, for `Pipeline` and `Submodel` alike, and from an annotated
assignment as from a plain one. Refuse, with the file and line, a constructor
call the reader cannot account for in full: an unknown keyword, a third
positional argument, or a name that is not a string literal.

**Acceptance:** Parsing then generating each of the four spellings (name by
position or keyword, description by position or keyword) gives the same name
and description back; an annotated constructor assignment does too. A
constructor with an unknown keyword raises a `ParseError` naming the line and
is never saved over.

**Dependencies:** None.

**Evidence:** `src/haute/_ast_helpers.py::_extract_meta` reads `call.args[0]`
and the `description` keyword only; `src/haute/_ast_helpers.py::_constructor_call`
skips an annotated assignment; `src/haute/pipeline.py::Pipeline` accepts both
forms; `src/haute/codegen.py::graph_to_code` writes the name it was given.

### CGEN-02 — A decimal membership with text that is not a number stays code
**Why:** Reproduced. The step lift recognises
`pl.Series([...]).str.to_decimal(scale=...)` inside an `is_in` and converts
each string with `int()` or `float()`. A string that is not a number raises a
bare `ValueError`, which is neither the lift's own "not a step" signal nor the
`StepLiftError` the config builder catches, so it leaves the parser. A
transform holding
`df = df.filter(pl.col("x").is_in(pl.Series(["abc"]).str.to_decimal(scale=2).implode()))`
makes `parse_pipeline_source` fail with
`ValueError: invalid literal for int() with base 10: 'abc'`, naming no node,
file or line; `"1e5"` and an empty string do the same. The module's contract
is that every statement it does not recognise is kept as code.

**Plan:** `_decimal_value` signals "not a step" for text it cannot convert.
Make the recogniser treat any failure of a step reader as "not a step", so a
reader's defect can cost a step its form but never the file.

**Acceptance:** A pipeline holding the statement above parses, the node keeps
the statement as free code, and generating the graph writes it back unchanged.
A test drives each step reader with a malformed literal and asserts none of
them raises out of the lift.

**Dependencies:** None.

**Evidence:** `src/haute/_polars_steps_lift.py::_decimal_value`;
`src/haute/_polars_steps_lift.py::_attempt` catches the lift's own signal only;
`src/haute/_config_builder.py::_with_lifted_steps` catches `StepLiftError` only.

### CGEN-03 — A closing return after a semicolon survives non-ASCII text
**Why:** Reproduced. The rewrite of a closing `return <expr>` into an
assignment slices the source line at `node.col_offset`, which Python counts in
UTF-8 bytes, as if it were a character index. A node body ending
`s = "é"; return df.head(1)` raises
`AssertionError: expected 'return' at line 1 col 10` from inside the parser.
The two neighbouring helpers convert the byte offset before slicing. The line
is marked as not needing coverage, so no test reaches it.

**Plan:** Slice the encoded line at the byte offset, as
`_follows_a_semicolon` and `_strip_outer_trailing_return` do, and remove the
coverage exemption with a test of the line.

**Acceptance:** The body above parses, and the node's code reads
`s = "é"` then the assignment of `df.head(1)`. A non-ASCII comment or string
before a semicolon-joined `return` round-trips through parse and generate.

**Dependencies:** None.

**Evidence:** `src/haute/_code_extraction.py::_rewrite_outer_returns_as_assignment`;
`src/haute/_code_extraction.py::_follows_a_semicolon` and
`src/haute/_code_extraction.py::_strip_outer_trailing_return` slice the
encoded line; `src/haute/_ast_helpers.py::_function_body_source` does too.

### CGEN-04 — Code generation reads each function's parameters once
**Why:** Measured. The module renderer builds its `connect` lines in a
comprehension that calls `_positional_parameters(functions)` for every edge,
and that call parses every node function again. On a chain of four-line
transforms, generating 25, 50, 100 and 200 nodes took 0.03, 0.16, 0.41 and
1.37 seconds, 1.30 of the last in that comprehension (40,002 `ast.parse`
calls). The cost grows with nodes times edges times body size and is paid on
every save.

**Plan:** Compute the parameter table once before the comprehension and index
it per edge.

**Acceptance:** Generating the 200-node chain parses each function a fixed
number of times, asserted by counting `ast.parse` calls, and the generated
text is byte-identical to today's for the existing golden pipelines.

**Dependencies:** None.

**Evidence:** `src/haute/codegen.py::_render_module`;
`src/haute/codegen.py::_positional_parameters`.
