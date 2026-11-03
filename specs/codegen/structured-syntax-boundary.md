# Structured Python Syntax Boundary

**Status:** Accepted
**Date:** 2026-08-31

## Context

Haute's Python pipeline surface has two simultaneous obligations. It must preserve
human-authored formatting, comments, preamble, and recovery evidence, while also making
semantic decisions about decorators, calls, expressions, pipeline structure, and generated
runtime behaviour. Those obligations were previously spread across string templates,
`tokenize`, AST walks, regular expressions, and substring classifiers. Adding another local
text recognizer would increase the number of representations and retain false matches inside
comments or string literals.

Three boundaries were considered: a concrete-syntax-tree boundary, a versioned graph/source
IR, and the existing token/AST split.

## Decision

Haute adopts a structured valid-Python boundary with two deliberately different projections:

1. LibCST is the authority for surgical modification of valid Python and for syntax-aware
   call-site discovery when comments and original formatting must survive. The codegen-owned
   `haute._python_syntax` module hides LibCST types behind small typed results and raises a
   structured, value-free error with a source position when input is not valid Python.
2. The standard-library AST remains the authority for semantic extraction and evaluation
   where formatting is irrelevant: strict pipeline parsing, expression interpretation,
   generated-file compilation gates, and executable safety analysis. Callers do not unparse
   an AST back over authored source.
3. Invalid Python is never repaired or normalized through the valid-source boundary. The
   editor recovery path keeps its conservative AST skeleton model, source captures,
   diagnostics, and recovery spans, and presents a syntax-invalid file as source only.
   Strict execution and save paths continue to reject invalid syntax.
4. String templates remain the readable initial source generator. Any post-generation edit
   must use the LibCST boundary rather than a token splice, regex replacement, or substring
   mutation. Text search remains acceptable only for non-semantic display/search features.

This is a source-syntax boundary, not a new runtime or persistence format. `PipelineGraph`
continues to represent the supported graph domain, while Python source remains capable of
carrying authored preamble, preserved blocks, comments, and unsupported constructs.

## Ownership boundaries

| Concern | Owner and representation |
|---|---|
| Comments and formatting during a valid-source edit | LibCST in `haute._python_syntax`; all untouched syntax is emitted from the original CST. |
| Generated source | Per-node builders describe each node and one layout routine prints the module as `ruff format` would; nothing edits generated source afterwards; the final AST parse gate rejects any invalid emitted module. |
| Strict pipeline parsing and semantic evaluation | Standard-library AST plus the closed parser/evaluator models. AST source locations own valid-source semantic error spans. |
| Invalid-source recovery | `_pipeline_recovery`, over valid Python only; recovery is read-only evidence for the editor and never becomes execution authority. A syntax-invalid file is a `source_only` document whose span is the Python syntax error's. |
| Structured rewrite/classifier parse failures | `StructuredSyntaxError` with stable reason, one-based line, and zero-based column; the calling component maps that evidence to its public failure or conservative result. |
| Project discovery | Project configuration and strict pipeline parsing remain authoritative; this pilot does not promote content substrings into syntax authority. |
| Runtime/codegen node behaviour | `NODE_REGISTRY` remains the dispatch authority. A shared semantics declaration is used only for node types whose runtime and generated input behaviour are genuinely identical. |

## Rejected alternatives

- **Versioned graph/source IR as the immediate authority.** The graph model cannot represent
  arbitrary preamble, comments, preserved blocks, unsupported Python, or recovery fragments.
  Making it authoritative now would either discard authored material or require a second,
  near-complete Python syntax schema with migrations and dual-write rules.
- **Retain the token/AST split for mutation and add more recognizers.** `tokenize` can find a
  balanced delimiter but still leaves manual source splicing and formatting ownership at every
  caller. Substring classifiers also match comments, strings, and longer attribute names. This
  is not an adequate semantic boundary.
- **Use LibCST for execution semantics.** A concrete syntax tree preserves trivia but is not a
  safer or clearer evaluator. The existing AST validators and closed interpreters remain the
  appropriate semantic layer.
- **Run recovery output through LibCST.** Invalid documents cannot be represented by a valid
  CST. A fallback rewrite would obscure the original defect and weaken recovery spans.

## Consequences

Valid-source rewrites conserve comments and formatting outside the changed syntax node. A
syntax-aware classifier ignores lookalike text in comments and literals and can report exact
call-site spans. Parse failure is explicit; safety-sensitive consumers may conservatively
classify an unreadable fragment as unknown/unsafe, but may not fall back to substring meaning.

LibCST is not introduced into canonical runtime evaluation, and generated source is not
globally reformatted. Adding a future source mutation or classifier requires extending the
small structured boundary or documenting why the operation is non-semantic. A broader source
IR would require a separate decision with conservation and migration evidence.

## Pilot and evidence

The pilot has three bounded parts:

- contract decorator injection migrates from `_matching_close_paren` plus manual text splicing
  to `inject_decorator_keyword`, with differential fixtures covering single-line, multiline,
  bare, comment-bearing, CRLF, adversarial string, invalid-source, and recovery inputs;
- trace row-reorder detection migrates from `_ROW_REORDERING_TOKENS` substring search to exact
  LibCST method-call sites, with tests proving that real calls are detected while comments,
  strings, longer attribute names, and bare attributes are not;
- the modelling node's first-input passthrough semantics and decorator metadata come from one
  registry declaration consumed by both runtime and codegen, with a direct cross-path result
  contract. No other node type is claimed to share those semantics without equivalent proof.

Round-trip tests continue to cover all supported node types, hand-authored formatting,
preserved blocks, strict parsing, and editor recovery. The pilot is accepted only when the
targeted codegen, parser/recovery, trace, registry, and execution-equivalence suites remain
green.

## Amendment (2026-09-26)

Codegen now describes each node structurally (decorator keywords, parameters, body) and
prints the module through one layout routine, so a contract keyword is rendered with its
decorator and no generated source is edited after the fact. The pilot's
`inject_decorator_keyword` and the repair path's `prepend_function_statements` therefore
have no callers and were removed. Rule 4 still binds: a future post-generation edit goes
through the LibCST boundary rather than a token splice or substring mutation.

## Rebuilding steps from code (READ-21)

`haute._polars_steps_lift` is a prototype of the inverse of the step renderer
(`haute._polars_steps.render_node_steps`). `lift_steps(node_type, code)` rebuilds a stepped
node's steps from its rendered body, and `canonical_steps(node_type, steps)` gives the steps
that body rebuilds as. Neither the parser nor the save path calls them yet; `READ-22` builds
on them.

This fits the boundary above. The standard-library AST is used only to propose steps.
Nothing is unparsed over authored code, and LibCST is not needed, since nothing is
rewritten. A statement becomes a step only when the renderer, given that step after the
steps rebuilt before it, prints exactly the statement's text. Every other statement keeps
its text as code. The rebuilt steps never take authority over Python they cannot describe.

### Canonical form

- **Statements and trivia.** A body is its top-level statements. A comment or blank line
  belongs to the statement below it, and lines after the last statement belong to that
  statement. Statements that share a line (`a = 1; b = 2`) are read as one statement of
  code.
- **Code.** A statement is code unless a step renders exactly its text where it stands. A
  statement with a comment or blank line above it is code, because no step holds a
  comment. A run of code statements, with the comments and blank lines among them, is
  one `free_code` step, however many statements it holds.
- **A step stands only where it renders the same.** A variable kept as code defines
  nothing for the steps after it, so a step that reads it is code too. A variable step
  whose name an earlier rebuilt variable already defines is code.
- **Ids.** Original step ids are dropped. Each rebuilt step's id is its kind and its
  one-based place in the list, such as `filter-3`, so the list renders as it stands.
- **Display-only text.** A formula's `text` is dropped.
- **Settings the code writes out are written out.**
  - A null literal carries `"value": null`.
  - A text join (`concat`) carries its `separator`, `""` when none was given.
  - Each `orderBy` entry of a window carries `descending`.
  - A `rank` or `dense_rank` window always carries `descending`.
- **Settings the code does not hold are left out.**
  - `descending` on any other window is left out.
  - `quantile` is left out unless the aggregate is `quantile`, on a window, a group-by
    aggregate and a column-type aggregate alike.
  - A group-by row count (`len`) has no `column`.
  - An empty `dtypes` on `select` or `drop` is left out.
  - A `where` or `orderBy` of `null` is left out.
- **Membership numbers.** Membership numbers are stored as their `numberType` writes them:
  - `integer` as whole numbers;
  - `float` as floats;
  - `decimal` as the number the rendered text reads as. A whole number is an integer,
    except `-0`, which only a float renders. Any other value is a float.

### Ambiguous forms

| Code | Steps that render it | Chosen step |
|---|---|---|
| A group-by whose every aggregate is `pl.col(v).filter(pl.col(on) == <literal>).<agg>()`, with one `v`, one `on` and one `agg` | a filtered group-by, or a pivot | the pivot |
| Two code steps side by side | either split, or one | one `free_code` step |
| `pl.when(d != 0).then(r / d)`, with no `otherwise` | the `divide` function (an if-then always ends in `otherwise`) | `divide` |
| One condition in a filter, a row filter or an if-then branch | `match: all` or `match: any` | `match: all` |
| An operand `{"kind": "expr", "expr": {"type": "operand", "operand": X}}` | the nested form, or `X` | `X`, where `X` renders the same in that slot and may stand there; otherwise the nested form |
| An expression `{"type": "operand", "operand": {"kind": "expr", "expr": E}}` | the wrapper, or `E` | `E` |
| A code statement that is exactly what a step renders | `free_code`, or the step | the step |

**Where `X` renders the same.**
- In expression position (a binary's left side, a function's input, an if-then's
  branches, text-join and sum parts), it always does.
- In value position (a binary's right side, a condition's value, an operand argument, a
  fill value), only a column, an expression, a date or a null does. A number, text or
  true/false is written bare there, and `pl.lit(...)` only when nested.
- A window's value takes only a column or an expression.
- A slot that takes only some literal types keeps a different literal nested: text tests
  take only text, the `clip` and `divide` functions' arguments only numbers, and the
  `fill_null` function's argument only a number, text or true/false. So `pl.lit(None)` in
  `str.contains(...)` is the nested form.

### Limits the prototype records

- **A comment above a step turns it into code.** `READ-22` needs a step to hold the
  comment above it before "comments never turn a node into code" can hold. That is a
  change to the step schema and the renderer.
- **A variable can be defined twice.** The renderer accepts a variable step whose name
  free code above it already assigns, as in `rate = 2` written as code before a `rate`
  step. Rebuilt, the first assignment is the variable and the step is code.
- **Only the exact rendered body is rebuilt.** `lift_steps` refuses code that its steps do
  not render back byte for byte, such as a body with a trailing newline, raising
  `StepLiftError`.

`tests/test_polars_steps_lift.py` proves both properties with Hypothesis:
- `lift_steps(render(s)) == canonical_steps(s)`;
- `render(lift_steps(render(s))) == render(s)`.

They hold over every step kind in `STEP_KINDS`, every function, window aggregate and
condition operator, and chains on a transform, a Data Input and an External File. The
chains mix steps with code, compound statements and comments. Every corpus translation
also rebuilds step for step.
