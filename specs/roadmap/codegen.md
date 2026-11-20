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

`CGEN-01` is a valid hand-written file the parser reads wrongly.
`CGEN-04` is a cost, not a wrong result.

`CGEN-05` to `CGEN-07` were reproduced on 10 November 2026 through the
parser, the preview and `haute run`. `CGEN-05` and `CGEN-06` are each a
hand-written form the parser accepts and then drops; `CGEN-07` is one the
naming check cannot see.

`CGEN-08` was reproduced on 11 November 2026 through the editor's load and
the commands: a syntax error the loader reports as a fault of its own.

`CGEN-09` and `CGEN-10` come from phase 5 of
[the codebase review plan](codebase-review-plan.md): a reader from a second
model family (Codex) read the lift of code into steps, which is new since
the 31 October 2026 audit, and the review reproduced both on 13 November
2026 through the editor's load and save routes, `CGEN-09` in a browser too.
Each was a save that rewrote code nobody edited. Since 17 November 2026 a
save, and since 20 November 2026 every request that carries the editor's
document, reads a node's steps only when they were edited ("Which parts a
request reads" in the code generation specification), so what is left of
each is a rendering that differs from the code it was read from.

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| CGEN-01 | Planned | P2 | A pipeline's name and description are read however the constructor gives them, and a constructor the reader cannot read is refused instead of rewritten. |
| CGEN-04 | Planned | P3 | Code generation reads each node function's parameters once per save, not once per edge. |
| CGEN-05 | Planned | P2 | A Transform's decorator refuses a keyword it does not know, so a misspelt setting is not ignored and then deleted on save. |
| CGEN-06 | Planned | P3 | A pipeline file that imports Polars under another name runs as plain Python runs it, or is reported when it is opened. |
| CGEN-07 | Planned | P3 | A name bound through `exec` or `globals()` in support code is refused by name, so it cannot silently replace another file's value. |
| CGEN-08 | Planned | P3 | A line indented with a tab, or a space too far, is reported as a syntax error at its line, not as an internal error with an incident id. |
| CGEN-09 | Planned | P1 | A step list or a node's settings read after a browser held them keep the numbers they were read with: `1.0` stays a decimal and a 16-digit integer keeps its digits. |
| CGEN-10 | Planned | P3 | A Transform that rebinds its input's name loads that statement as code, so its steps render against its input names. |

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

### CGEN-05 — A Transform's decorator refuses a keyword it does not know
**Why:** Reproduced on 10 November 2026. `@pipeline.polars(selected_colums=["k"])`,
with `selected_columns` misspelt, parses without a word. The node then passes
every column: its preview showed `k` and `x`, where the correctly spelt
keyword showed `k`. The next save from the editor writes the decorator back
as `@pipeline.polars`, so the misspelt setting disappears from the file. Any
unknown keyword goes the same way, `cache=True` or `description="..."`
included. Every other node type refuses this by name: an Edge Join answers
"Node 'j' has edgeJoin config keys the node type does not declare", and a
node with a settings file refuses extra decorator keywords. A Transform reads
its code from the function body and copies only `selected_columns` and
`input_mapping` from the decorator; nothing looks at the rest. Running the
file on its own keeps the unknown keywords in the node's settings, so the
two readings of one file differ. One accepted value is lost as well: an Edge
Join's `suffix=""` parses as an empty suffix and is written back without the
keyword, which then reads as the default `_right`. The 28 October audit
recorded the lead without naming a keyword.

**Plan:** Refuse a Transform keyword outside its declared set when the file
is parsed, naming the node and the keyword, in the words the Edge Join uses.
Write an Edge Join's empty suffix back as it was given, or refuse it by
name.

**Acceptance:** The misspelt file is refused naming node `t` and
`selected_colums`. A file holding only declared keywords parses and
regenerates to itself. An Edge Join with `suffix=""` reads the same after
parse, generate and parse, or is refused by name. A test gives every node
type one unknown decorator keyword and expects a refusal that names it.

**Dependencies:** None.

**Evidence:** `src/haute/_config_builder.py::_build_node_config`;
`src/haute/_config_builder.py::_transform_decorator_config`;
`src/haute/_node_config_models.py::COLUMN_CONFIG_KEYS`;
`src/haute/_edge_join.py::edge_join_config_to_decorator_kwargs`.

### CGEN-06 — Polars imported under another name is kept, or refused by name
**Why:** Reproduced on 10 November 2026. A pipeline file that starts
`import polars` or `import polars as plr`, and whose nodes use that name, is
valid Python and imports under plain Python. Haute parses it with no
diagnostic, then every node that uses the name fails: the preview shows
"name 'plr' is not defined", and `haute run` and the deploy dry run exit 1
on the same `NameError`. A save from the editor writes the header back as
`import polars as pl` and leaves the nodes using a name nothing binds. The
reader treats any `import polars`, whatever its alias, as the standard
import that the generated `import polars as pl` stands in for, so the alias
is dropped and the check for statements a save would drop does not report
it. The specification's rule works for the neighbouring case:
`from polars import col` above `import haute` is reported as a statement a
save would drop, naming its lines and the two fixes, and the same line below
`import haute` is kept as preamble and runs. The 28 October audit recorded
the `pl`-only recognisers as a lead; a data type spelt
`pl.datatypes.String` gave the same rows everywhere and only cost the
deployment its one-frame proof, which the specification allows.

**Plan:** Count only `import polars as pl` as the standard import. Keep any
other import of Polars as preamble where it sits below the standard imports,
and report it as a statement a save would drop where it sits above, as the
`from` form is.

**Acceptance:** A file starting `import polars as plr` either runs in the
editor's preview and from `haute run` with the rows plain Python gives, and
keeps its import through a save, or is reported when it is opened, naming
the line and the fix. A test holds `import polars`, `import polars as plr`
and `from polars import col`, above and below `import haute`, to one of
those two outcomes each.

**Dependencies:** None.

**Evidence:** `src/haute/_ast_helpers.py::_is_standard_import`;
`src/haute/_ast_helpers.py::unkept_module_statements`;
`src/haute/_user_exec.py::_exec_user_code`;
`src/haute/_polars_call_shapes.py::is_pl_dtype_reference`.

### CGEN-07 — A dynamic binding in support code is refused by name
**Why:** Reproduced on 10 November 2026. The naming check refuses two
support-code sources that bind one name to different things, because once a
submodel's preamble is appended to the root's only the last would be seen.
It reads the bindings from the source without running it. A submodel
preamble that binds through `globals()["RATE"] = 2` or `exec("RATE = 2")`,
beside a root preamble's `RATE = 3`, passes the strict parse, and a root
node that reads `RATE` then computes with 2: its preview showed 2 on both
rows, where Python running the two files as modules gives 3. The same
collision written as an assignment is refused. The specification refuses
whatever keeps the inventory of names incomplete, naming the statement, and
lists the `__all__` and star-import cases; a module-level `exec`, or a write
through `globals()`, `vars()` or `locals()`, is not among them and is not
refused. Project code is trusted, so this is no hole in a sandbox. It is a
check for honest mistakes that one unusual but legal line switches off. The
28 October audit recorded the lead without a witness.

**Plan:** Count a module-level `exec` or `eval` call, and a write through
`globals()`, `vars()` or `locals()`, in support code as a statement that
keeps the inventory incomplete. Refuse it naming the statement and the
file, as a computed `__all__` is refused, and say to bind the name with an
ordinary assignment.

**Acceptance:** Each of the two submodel preambles above is refused wherever
the naming rule already runs, naming the line. A preamble that only reads
`globals()` is not refused. A test holds each dynamic form to the refusal.

**Dependencies:** None.

**Evidence:** `src/haute/_support_code_names.py::support_code_inventory`;
`src/haute/_support_code_names.py::support_code_violations`;
`specs/codegen/high-level.md`.

### CGEN-08 — A mis-indented line is a syntax error, not an internal error
**Why:** Reproduced on 11 November 2026 through the editor's load,
`haute lint` and `haute run`. One line of a node's code is indented with a
tab where its neighbours have four spaces, as Notepad's Tab key leaves it,
or by five spaces after a paste. Python says where: "unindent does not match
any outer indentation level", at line 22. For the tab, `haute lint` and
`haute run` say "Pipeline source contains invalid Python syntax." with that
line. The editor shows the source alone under the diagnostic
`pipeline_recovery_internal_error`: "The pipeline source could not be
reconstructed because of an internal error.", with the remedy "Check the
server logs with the incident id and report the defect." and no line. Other
slips in the same file are reported as they should be, each as
`python_syntax_error` at its line: a missing bracket, a line not indented at
all, a non-breaking space pasted from a web page.

The cause is the diagnostic's span, not the parse. For "unexpected indent"
and for "unindent does not match any outer indentation level" Python gives
an end offset of -1. The loader turns the error's offsets into a span and
clamps each to zero, so the span ends at column 0 of the line it starts on
at column 4 or later, and the span's own rule, "Recovery source span must
end at or after its start.", raises inside the handler of the syntax error.
The loader's outer catch then reports an internal error.

**Plan:** Build the syntax error's span so that it cannot end before it
starts: when Python gives no end, or a negative one, the span ends where it
starts. Keep the internal-error diagnostic for failures that are Haute's.

**Acceptance:** A pipeline file with a tab-indented line among
space-indented ones, and one with a line indented by five spaces, each load
as source only with `python_syntax_error` at the line Python names and no
incident id. A test runs each indentation error Python raises through the
loader.

**Dependencies:** None.

**Evidence:**
`src/haute/_pipeline_recovery.py::_load_readable_pipeline_editor_document`;
`src/haute/_pipeline_recovery.py::_span`;
`src/haute/schemas.py::RecoverySourceSpan`.

### CGEN-09 — A part read from the browser keeps the numbers it was read with
**Why:** Reproduced on 13 November 2026 in a browser and through the
editor's routes; what is left of it read again on 20 November 2026. A
Transform written by hand holds `pl.lit(1.0).alias("relativity")`,
`(pl.col("value") * 2.0)`, `pl.col("value").fill_null(0.0)` or a filter
`pl.col("id") == 9007199254740993`. Its steps carry each number literal as
a JSON number, and a browser has one kind of number: `1.0` arrives as `1`,
and an integer above 2^53 as its nearest double. Since 20 November 2026
every request that carries the document reads a node's steps and its
settings from the browser only when they were edited, and takes every other
part from the saved form the server gave ("Which parts a request reads" in
the code generation specification). But whenever a request reads the
steps from the list the browser held, the renderer writes an integer for
an integer: the analyst edits another step of the node, or undoes back to
its steps after a save, or renames a node it reads, which rewrites the
input name in its steps, or a submodel is made around it or dissolved,
which gives the node a new place. The same holds for a node's settings once
one of them is edited: the others are written as the browser held them, so
a Rating Step whose output column is renamed has its `default_value: 1.0`
written `1`. The file then
holds `pl.lit(1)`, `* 2`, `fill_null(0)` and `== 9007199254740992`. Run on
the same rows, the first column is 32-bit integer where it was 64-bit
float, the second and third are 64-bit integer where they were float, and
the filter returns the row of another id. The changed type reaches whatever
reads the column: a deployment's output schema, a Data Output's column
type, and arithmetic that was written to be decimal.

**Plan:** Carry a number of a step or a setting across the wire in a form
that keeps how it was written, its kind and its digits as text, as the
Workbench does for integers a browser cannot hold, and render and write
from that.

**Acceptance:** A test passes every rebuilt step list through a number
round trip as JavaScript performs it and requires the rendered code to
equal the source, for whole-number decimals, exponent forms, negative zero
and integers of 16 to 19 digits, and the same round trip of every shipped
example's settings requires each settings file a save writes from them to
equal the file. A browser journey that edits one step of a Transform holding
the four statements above keeps the other three as they were written, and
one that renames a Rating Step's output column keeps its `default_value`
`1.0`.

**Dependencies:** None.

**Evidence:**
`src/haute/_polars_steps_lift.py::_bare_literal`;
`src/haute/_polars_steps.py::_Renderer`;
`src/haute/_config_builder.py::_with_lifted_steps`;
`src/haute/_codegen_builders.py::_stepped_body_code`;
`frontend/src/panels/editors/polarsSteps/useRenderedSteps.ts`;
`frontend/src/panels/editors/polarsSteps/PolarsStepsEditor.tsx`;
`specs/codegen/structured-syntax-boundary.md`;
`src/haute/_edited_parts.py::graph_as_read`;
`frontend/src/utils/documentRequest.ts::documentRequestGraph`.

### CGEN-10 — A Transform that rebinds its input's name loads that statement as code
**Why:** Reproduced on 13 November 2026 through the editor's load and save
routes; what is left of it read again on 17 November 2026. A Transform whose
input is named `exposure` holds `df = exposure`, then `exposure = 365`, then
`df.with_columns((pl.col("days") / exposure).alias("years"))`. It runs and
gives 1.0 and 2.0, and since 20 November 2026 a save or a preview of it
untouched keeps or runs its code ("Which parts a request reads" in the code
generation specification). But the lift reads `exposure = 365` as a
Variable step. It accepts a step when the step, rendered after the steps
before it, gives the statement's text, and it makes that trial without the
node's input names. A render with them refuses a variable named after an
input: the editor marks the step unfinished, and once the analyst edits any
step of the node the save writes `raise NotImplementedError(` with "This
node's steps are incomplete. Complete or remove them before running." in
place of the code. What the lift accepts and what a render against the
inputs accepts are two rules.

**Plan:** Give the lift the node's input names, so a statement a render
against them would refuse stays code.

**Acceptance:** The Transform above loads with `exposure = 365` and the
line after it as code, a save after an edit to another step keeps them, and
the node runs. A test renders every lifted list with its node's input names
and requires the source back.

**Dependencies:** None.

**Evidence:**
`src/haute/_polars_steps_lift.py::lift_steps`;
`src/haute/_polars_steps.py::_Renderer`;
`src/haute/_codegen_builders.py::_stepped_body_code`;
`src/haute/_config_builder.py::_with_lifted_steps`.
