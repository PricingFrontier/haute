# Expression Parsing — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/parser.py` | Strict public entry points `parse_pipeline_file` / `parse_submodel_file` / `parse_pipeline_source`. Orchestrates AST metadata/node/edge extraction, submodel resolution + merge, conservation, and graph-shape validation for valid pipeline source; whole-file syntax errors raise contextual `ParseError`. `load_declared_global_constants` loads the global constants file a constructor declares, through an injected byte reader when editor recovery supplies its capture, and returns the reason instead of failing when the file is missing, unreadable, invalid or outside the pipeline folder; the parser records it as `global_constants_error` and in the graph warning. |
| `src/haute/_parser_conservation.py` | Strict fail-loud acceptance gate. Verifies that parsed root node IDs, ordered edge/handle identities, submodel references, and cross-boundary endpoints conserve the authored structure; also builds the deterministic missing-submodel diagnostic. |
| `src/haute/_parser_bindings.py` | Strict parameter binding gate: every parsed Polars node's positional parameters must equal its connected executable input names after `inputMapping`, in parent files (`assert_polars_parameters_bound`, after the merge) and definition files (`assert_definition_parameters_bound`, which the submodel parse runs on its own file, public input ports binding the sanitised port ID); any other shape is a `ParseError` with `node_id`, `unbound_parameters`, `unconsumed_inputs`, `connected_inputs` and `remediation`, plus `source_file` for a definition file (F13). |
| `src/haute/_parser_submodels.py` | `submodel_imports` / `extract_submodel_registrations` / `parse_submodel_source` / `merge_submodels`: reads a pipeline file's submodel imports (`submodel_imports`: each name of `from modules import a, b` bound to its file `modules/<name>.py`; any other spelling of an import from `modules`, a module imported twice and a name `submodel_module_name` refuses raise `ParseError`), extracts each explicit registration through that table (`extract_submodel_registrations`: the first argument `<module>.submodel` for an imported module, giving a `SubmodelRegistration` whose `path` is the module's file and `binding` its imported name; the removed file-path form is refused with the import form as remediation, an unimported module is refused, and reading a whole file refuses an import no registration uses, while a caller passing `imports=` reads one statement at a time), parses each referenced submodel file into its own `PipelineGraph` (`_validate_pipeline_dir` checks the constructor's way back to the pipeline against where the file sits; a registration in it is nested, refused with `nested_lines`), and merges canonical occurrences into the parent (hierarchical or flattened). |
| `src/haute/_expression_parser.py` | `parse_expression` / `evaluate_expression` / `parse_expression_chain` and their supporting classes: AST-based conversion of a Polars with-columns expression to human-readable text (`_ExprConverter`), and Polars evaluation of the located expression on the traced row (`_locate_defining_expression`, `_row_value`, `_branch_selection`). `evaluate_expression` shows each scalar global constant the formula reads (a date as `pl.date(...)`) as its value, never touching quoted text, and inlines it before evaluation (`_global_constant_values`, `_inline_global_constants`); `parse_expression` never counts `global_constants.<name>` as a referenced column. |

## Key types and data structures

- **`ParsedExpression`** (dataclass, `_expression_parser.py`) — `target_column`, `expression_text`,
  `expression_type`
  (`"arithmetic"|"conditional"|"horizontal_func"|"function_call"|"window"|"opaque"`),
  `referenced_columns: list[str]`, `constants: list[Any]`, `sub_expressions: list[ParsedExpression]`
  (nested conditionals inside a `then`/`otherwise` arm), `source_line: int | None`.
- **`EvaluatedExpression`** (dataclass, extends `ParsedExpression`) — adds `substituted_text`,
  `result_value`, `not_computable_reason: str | None`, `input_values: dict[str, Any]`, and
  conditional-branch metadata
  (`taken_branch`, `taken_branch_index`, `dimmed_branches: list[int]`,
  `nested_branches: list[str]`).
- **`PipelineGraph`** (`src/haute/_types.py`, owned by
  [server-api](../server-api/low-level.md) and produced here) — `nodes`,
  `edges`, `pipeline_name`, `pipeline_description`, `preamble`,
  `preserved_blocks`, `source_file`, `source_revision` (server-populated
  live-document metadata), `warning`, `submodels`, `sources`, `active_source`.
  Canonical structure shared with the executor, codegen, deploy, and the server
  API layer.
- **`_ExprConverter`** (`_expression_parser.py`) — one AST-node-type-dispatch method per handled
  node kind; accumulates `columns`, `constants`, `expr_type`, `sub_expressions`, and an
  `_is_opaque` flag as it walks. Takes an optional `symbol_table` for top-level variable
  resolution and a `_resolving` set that guards against infinite recursion on self-referential
  names.
- **`AssignmentPhases`** (frozen dataclass, `_expression_parser.py`) — `before` and `at_or_after`,
  the columns a node's `with_columns` calls assign on either side of the call that last assigns a
  target column (`assignment_phases(code, target_column)`). Every expression in one call reads the
  frame from before it, so trace enrichment reads the second set at its earlier values. An
  unaliased expression is named after its leftmost input column through methods, arithmetic and
  the name-keeping namespaces (`_implicit_output_name`); a call's writes count only explicit
  names and that strict inference, never the display parser's first-column guess. A write at or
  after the target's call whose name cannot be determined (several columns, a selector list or
  regex, a rename, a dynamic alias, a comprehension, another function) sets `unresolved`; one in
  an earlier call sets `unresolved_before`.
- **`_RowValue`** (frozen dataclass, `_expression_parser.py`) — one expression's `value` on the
  traced row, or the `reason` one row cannot give it. `_row_value(node, row, namespace)` produces
  it, and `_branch_selection` uses it for each `when` condition.
- **Precedence tables**: `_OP_SYMBOLS`, `_CMP_SYMBOLS`, `_PREC` (binary operators) plus synthetic
  constants `_PREC_COMPARE`, `_PREC_BOOL_OR`, `_PREC_BOOL_AND`, `_PREC_IFEXP`, `_PREC_USUB` for
  node kinds that are not `ast.BinOp` but still need correct parenthesisation when nested as an
  operand — comparisons and boolean operators bind looser than any arithmetic operator, a
  conditional (`a if c else b`) binds loosest of all.
- **`errors.ParseError`** / **`errors.ConfigError`** (`src/haute/errors.py`, shared across the codebase) —
  the two exception types this component deliberately raises; see Error handling.

## Control flow

**`parse_pipeline_source`** (`parser.py`): `ast.parse(source)` → on `SyntaxError`, raise a
contextual `ParseError` naming the source and syntax location → otherwise extract pipeline meta /
decorated nodes / connect edges / preamble (without the file's imports from `modules`,
which codegen writes) / preserved blocks using the AST
primitives from `src/haute/_ast_helpers.py` → collect labels from any node whose
config carries `_load_error` into a graph-level `warning` (nothing in `src/haute`
produces that key; the config path raises `ConfigError` instead, so the step is reached
only by tests that plant the marker) → extract the submodel registrations through the file's
submodel imports (`_parser_submodels.extract_submodel_registrations`, which refuses a
non-canonical import or registration and an unused import) → if any
were found, require `_base_dir` or `_submodel_base_dir` (otherwise
raise with every unresolved path), resolve each registration's `modules/<name>.py` within the
project, raise one `ParseError` listing every missing file, group registrations by resolved file
(`submodel_source_key`), parse
each definition file once (one definition id in two files is a `ParseError`), and call `_parser_submodels.merge_submodels` → run
the structure-conservation gate → `validate_pipeline_graph_shape_contracts` (owned outside this
component) → log `pipeline_parsed`.

**`merge_submodels`** (`_parser_submodels.py`): validate that every parsed
child file has literal structured input/output ports present and extract its
declared `definition_id`. Build one `SubmodelDefinition` per definition id and
one `SUBMODEL` occurrence per registration; each occurrence uses its name as
node id (`node.id == label == alias == name`), config `{definitionId, alias}`, and
derives its node label directly from the name (registrations accept `(<module>.submodel, name, *, instance_of=None)`
and reject `definition_id=`, `instance_id=`, `alias=`, and `label=`).
Parent connect endpoints use names and declared
public port names, which become `in__<name>`/`out__<name>` graph handles;
internal child ids are never accepted as parent endpoints. Only when
`flatten=True` is the canonical hierarchical graph passed to
`flatten_graph`. A submodel file containing its own registration, whatever its
arguments, raises `ParseError` with the registrations' lines (`nested_lines`),
preserving the one-level nesting contract.

**`parse_expression`** (`_expression_parser.py`): strip a leading BOM → `_cached_parse(code)` →
bail to `_opaque(...)` on empty input or `SyntaxError` → `_has_control_flow_wrapping_target`: if
the `with_columns()` producing `target_column` sits inside `if`/`for`/`while`/`try`/`with`/`match`,
bail to opaque (the value is branch-dependent and cannot be statically resolved) →
`_find_definition` scans every call `_calls_with_bindings` returns, "last alias match wins", an
unaliased expression named after its leftmost column by `_infer_auto_name` →
`_find_control_flow_assigned_vars` records names assigned *inside* control flow, so an expression
referencing one of those, or one unprovable where it runs, is opaque even if the same name also
has a top-level binding → convert the winning expression AST with `_ExprConverter` over the
bindings in force at its statement.

**`_calls_with_bindings`** (`_expression_parser.py`, memoised per code string): one pass over the
top-level statements gives every `with_columns()`/`select()` call (`_find_with_columns_calls`,
ordered by (line, col, end_line, end_col) source span so a chained
`df.with_columns(A).with_columns(B)` — which `ast.walk`'s breadth-first parent-before-child order
would otherwise visit outer-call-first — comes back in true execution order) the names bound
before its own statement (`_statement_index`, by source position, so `a = 1; df = ...` on one line
counts). `_bind_statement` folds each statement in order: a simple `name = value` or
`name: type = value` binds the value with the names it reads resolved as they stand
(`_substitute_names_in_ast`, every loaded name in any node), so a later rebinding never changes it
(`expr = pl.col("base"); expr = expr * pl.col("f")` folds as it runs), and a binding that reads an
unprovable name is unprovable; a frame assignment (`df = df.filter(...)`) binds nothing; any other
statement that assigns a name (control flow, an augmented or unpacking assignment, a `with` or
`for` target, a walrus) drops it from the table and marks it unprovable. An import binds a module
and a definition a callable, and neither changes the table. An argument naming a bound variable
yields that variable's binding, already resolved where it was bound, and no later table resolves
the names left in it (`bound_name` on `_Definition`, and the converter renders a binding's value
with no table).

**`evaluate_expression`** (`_expression_parser.py`): wrap bare dot-chain code (`.method(...)`),
including snippets with leading indentation, or a naked expression into a synthetic `df = (...)`
statement through `wrap_node_code`, after stripping a leading BOM — the one wrapper
`parse_expression_chain`, `assignment_phases` and trace enrichment use too, so every reader
parses the same source shape → merge
`preamble_ns` constants under `row_values` (column values win on key collision) →
`parse_expression` for the text/columns/type → `_definition_in` locates the expression assigning
`target_column` as `parse_expression` does → the expression is a window when it contains an
`.over()` call (`_is_window`), and then its string partition arguments (positional or
`partition_by`, a list spread) join its referenced columns (`_window_partition_columns`) →
`_ValueRenderer` writes the substituted text (see Edge cases) → the evaluation namespace drops the
names unprovable where the expression runs → `_resolve_names` resolves same-node names through the
bindings in force at its statement and `_inline_global_constants` inlines each scalar global
constant it reads → `_row_value` inlines
literal preamble constants, reports an unresolved free name, classifies the expression with
`chunking.classify_row_local_expression` (not row-local → `not_row_local: <operator>`), validates it
with `_sandbox.validate_user_code`, evaluates its source under `safe_globals(pl=pl, <preamble names>)`
(a non-`Expr` result becomes `pl.lit`), reports a root column missing from the row
(`column_unavailable: <column>`), and selects it on the one-row frame as a native value
(`to_list()[0]`, so a List cell is a list, not a Series) → for
`expression_type == "conditional"`, `_branch_selection` evaluates each condition the same way in
source order and records the taken arm, recursing into a taken arm that is itself a `when` chain for
`nested_branches`.

**`parse_expression_chain`** (`_expression_parser.py`): strip/wrap the code and parse it; a
`SyntaxError` is converted to `[parse_expression(...)]` when that opaque result exists (otherwise
`[]`) → parse every column definition across all
`with_columns()` calls exactly once into `parsed_by_col`/`refs_by_col` maps (reusing one shared
tree/converter pass rather than re-invoking `parse_expression` per chain element) → if
`target_column` was not defined anywhere, return `[]` → depth-first walk backward from
`target_column` through `refs_by_col`, appending each column post-order (so a dependency is
appended before the column that depends on it) → return the parsed expressions in that order.

## Edge cases and invariants

Parser internals consume only the current metadata contract. Every extracted
node supplies `param_names` and `edge_param_names`; consumers must not infer
missing positional-parameter metadata from the broader parameter list or an
empty default. Missing required metadata is an internal contract failure.
The conservation gate accepts `submodel_occurrence_paths` as its sole loaded
occurrence manifest. Removed keywords and unknown keywords are rejected, with
no alias or migration shim; direct test callers use the same current contract.

- **Execution-order vs. walk-order**: `ast.walk` is breadth-first (parent before child), which for
  a chained `df.with_columns(A).with_columns(B)` visits the *outer* call (B) before the nested
  inner call (A). `_find_with_columns_calls` re-sorts by source span specifically so "last match
  wins" logic picks the truly outermost/last-applied definition.
- **Self-referential cycles**: `x = x + 1` at top level leaves a `Name('x')` inside its own
  resolved value. `_ExprConverter._name`'s `_resolving` set detects re-entry into the same name and
  bails to an opaque atom instead of recursing to `RecursionError`.
- **Control-flow-conditional variables poison downstream use**: a variable assigned inside
  `if`/`for`/`while`/`try`/`with`/`match` has an ambiguous value at any point of use; `parse_expression`
  treats the whole target expression as opaque if it references such a name, even when the same
  name also happens to have an (irrelevant) top-level binding elsewhere. A name unprovable where
  the expression runs (assigned before it in a way the table cannot follow, or a binding that
  read such a name, directly or through another binding, and stays unknown when that name is
  bound again) makes it opaque too, and `evaluate_expression` reports `unresolved_name: <name>`
  for the first such name the expression reads instead of computing with a stale binding or a
  preamble value of the same name. A value changed in place (`d["k"] = v`, `d.update(...)`) is
  not followed: the table keeps the value as bound.
- **Values are Polars' own**: integer overflow wraps as the column's dtype does, division and
  modulo by zero, Kleene `&`/`|`, rounding and NaN handling are whatever Polars computes on the
  row, because Polars computes them. A typed null propagates through every operation; a native
  `None` passed without `row` becomes a `Null`-dtype column, which Polars rejects for numeric,
  temporal and string methods.
- **Row-local means registered row-local**: `classify_row_local_expression` admits an operation
  the Polars operation registry classes as row-local, whether or not the chunk classifier has a
  proof for it (`pl.min_horizontal`, `pl.format` and a `when` chained on a conditional are
  admitted), and keeps the chunk classifier's argument guards (`fill_null(strategy=...)`,
  format-inferring `str.to_date()`). An operation missing from the registry is not proven
  and reports `not_row_local`. `pow`, `replace_strict` and `dt.total_days` are registered
  row-local without a chunk proof; `replace_strict` is admitted only with `replace`'s
  literal mapping forms (plus a literal `default=` and a dtype `return_dtype=`), and an
  unmapped value without a default raises Polars' error, as it does for the column.
- **A bare string in `then`/`otherwise` is a column**: Polars reads `.then("high")` as
  `pl.col("high")`, so an arm written that way reports `column_unavailable: high` unless the row
  has such a column.
- **Substitution renders the syntax tree**: `_ValueRenderer` is the converter with three hooks
  that take a value — a column reference (`pl.col`, a `pl.format` argument, a horizontal
  function's listed column) the row's value, a free name bound to a literal preamble constant (not
  one unprovable where the expression runs) its value, and a `global_constants.<name>` read its
  scalar — so nothing outside those nodes changes: a quoted literal spelled like a column, a
  method named like one and a keyword argument stay as written, and a value is never re-scanned.
  A window renders as `_window_description`'s words from column names (`sum of premium over
  region`), parenthesised when it is not the whole expression (the expression a variable the
  call names is bound to counts as the whole one). A nested conditional's branches are rendered
  with values like the rest. Window type and partition columns come from the expression with
  its variables resolved, so `y=expr` with `expr` a window is typed a window. Given the traced row's typed frame,
  a Float32 column's value is shown with its float32 digits (`528.09`, not the widened
  `528.0900268554688`) by `_display_values`; only the text changes, and the values computed on
  stay exact.
- **Unary operators keep their grouping**: `_operand_prec` gives `-`, `+` and `~` the precedence
  `_PREC_USUB` (below `**`, above `*`) and `not` `_PREC_NOT` (below a comparison, above `and`);
  `_unaryop` parenthesises a looser operand, so `-(x + z)` and `~(a & b)` keep their parentheses
  while `-x ** 2` stays `-(x ** 2)` as Python reads it.
- **BOM handling**: `wrap_node_code` strips a leading `﻿`, and `parse_expression`
  and `_locate_defining_expression` strip it from code given to them unwrapped, so every
  reader parses the same source.
- **No text recovery of syntax-invalid source**: a file that is not valid Python is never
  scanned for fragments. The strict parser turns its `SyntaxError` into a contextual
  `ParseError`, and editor recovery presents it as a source-only document carrying the located
  syntax error, with no canvas.
- **Preamble boundaries are alias-aware**: a valid module uses AST statement line spans to stop
  before an aliased or multiline `Pipeline(...)` construction; neutral syntax recovery parses
  recoverable import lines independently, including comma-separated names and trailing comments,
  so both `import haute as <alias>` and `from haute import Pipeline as <alias>` preserve the same
  user preamble without capturing the constructor.
- **A pipeline file's submodel imports are generated code**: `is_submodel_import` recognises an
  import from `modules` in any spelling, and preamble extraction leaves out the lines of every
  such statement in a pipeline file, wherever it sits, because codegen rewrites the imports
  from the registrations; a file that does not parse has its `from modules ... import` lines
  left out textually, a parenthesised import to its closing line. In a definition file the
  same import is ordinary preamble.
- **Submodel imports have one spelling**: `from modules import <name>`, each module once and
  by its own name. A relative or dotted import, `import modules...`, an alias or `*`, a
  module imported twice, and a module whose name `submodel_module_name` refuses (not a
  canonical identifier as a node's is, starting with `__`, or a Python built-in) raise
  `ParseError` from `submodel_imports`; a keyword cannot be imported at all.
- **A registration's definition is an imported module's `submodel`**: its first argument is
  `<name>.submodel` for an imported `<name>`, and the derived path, `modules/<name>.py`,
  is all a registration records of its file. A string first argument or any `file=` is the
  removed path form, refused with the exact import form as remediation (or, for a path no
  import could name, with moving the definition into `modules/` first); the definition is
  positional, so `definition=` is refused, and an attribute of an
  unimported name is refused with a remediation adding the import. Reading a whole file
  refuses an import no registration uses; a caller passing `imports=`, which reads one
  statement at a time, gets no such check.
- **Nested submodels capped at one level**: a submodel file's own
  `pipeline.submodel(...)` calls, whatever their arguments, raise one `ParseError` listing their
  lines (`nested_lines`); they are never recursed into or omitted from an otherwise
  healthy-looking graph.
- **Missing submodel files are aggregated**: all resolved paths are checked before merge. One or
  more missing files produce one `ParseError` with deterministic `missing_paths` detail in
  authored order.
- **Submodel resolution roots are mandatory**: healthy in-memory parsing cannot conserve a
  `pipeline.submodel()` registration without `_base_dir` or `_submodel_base_dir`, so it raises with
  `unresolved_paths` instead of returning a root-only graph. Editor recovery resolves submodel
  fragments against the explicitly supplied project and parent-pipeline roots.
- **Repeated definition files are intentional for reusable occurrences**:
  registrations naming one imported module, or otherwise resolving to one file, are grouped
  and parsed once.
- **Definition/file identity is one-to-one**: one definition id resolving to
  multiple files, conflicting definition ids for one file, or an unreadable
  child raises `ParseError`.
- **Occurrence identity is explicit**: a definition other than an imported `<module>.submodel`,
  a missing/non-literal/blank/non-canonical `name`, rejected keywords (`definition_id=`,
  `instance_id=`, `alias=`, `label=`), and duplicate occurrence names fail before merge. No
  file/name/node-id inference is attempted.
- **Public boundary connections are explicit**: a parent `connect` endpoint
  that names an occurrence alias must also name a declared public port id.
  Function-parameter inference remains inside its owning root or definition
  graph; it never reaches through a definition interface or exposes an
  internal child id.
- **Conservation is an acceptance gate**: the parser compares authored root
  nodes and ordered edges, the registrations' derived paths and explicit identity fields,
  occurrence aliases and public port ids (a parent `connect` that names a
  definition-owned child id or any other unknown endpoint is rejected as
  dangling with its authored identity), and the constructed
  definition/occurrence registry. Each child source is conserved within its
  own definition graph. Exact duplicate `connect()` identities raise the
  dedicated diagnostic; every other difference raises `ParseError` before
  graph-shape validation or return.
- **Polars parameter binding is strictly conserved**: every Polars node's
  positional parameter set (after applying `inputMapping`) must equal the set
  of executable input names contributed by incoming edges (or public input port
  sanitised IDs for submodel definition nodes). Mismatches fail immediately
  with `ParseError("Pipeline function parameters do not match the node's connected inputs.")`
  naming the node, the unbound positional parameters, unconsumed inputs,
  connected inputs, and remediation guidance.

## Error handling

- **`ConfigError`** propagates from `src/haute/_config_builder.py` when a healthy parse cannot
  load/validate a referenced sidecar or a folder-backed node omits `config=`. These failures are
  not converted to `_load_error` nodes or graph warnings. Missing required Data Input/Output
  locator values are a completeness concern, not a `ConfigError`: the builder validates those
  configs with `require_complete=False`, so an empty or absent path/locator parses cleanly and
  is reported as field-level completeness by the editor document loader instead.
- **`ParseError`** (raised, not caught, by this component) for: a whole-file `SyntaxError`
  (an unclosed call among them), with its location; a decorator keyword argument that is not a
  literal (only
  Python literals and the sanctioned `Contract(...)` constructor are resolvable at parse time);
  `**kwargs` expansion in a decorator; an import from `modules` other than
  `from modules import <name>` (relative, dotted, `import modules...`, aliased, `*`), a module
  imported twice or whose name is not a canonical identifier, starts with `__` or is a Python
  built-in; a registration whose first
  argument is not `<module>.submodel` for an imported module, including the removed file-path
  form (positional or `file=`), whose `remediation` is the exact import form; an import no
  registration uses; an `async def` pipeline node
  function; a syntactically invalid referenced submodel file; a missing submodel file; a
  submodel registration without a resolution root; a conflicting definition/file registration; a missing or duplicate
  canonical occurrence identity; an invalid structured public-port contract; nested submodel references; an exact duplicate
  edge identity; a structure-conservation mismatch; a submodel path that escapes the project
  root; or, in the strict form only, once the graph is built, any
  `_executable_names.executable_name_violations` entry, the message from
  `format_name_violations`. `parse_pipeline_source_with_name_violations` returns those
  entries beside the graph instead, for the editor document loader. A folder-backed node with no matching `config=` kwarg raises `ConfigError` through
  `_sidecar_required_error`, consistently with other sidecar configuration failures.
- **`SyntaxError` from `ast.parse`** at the top of `parse_pipeline_source` is converted into a
  contextual `ParseError`; strict callers never receive syntax-recovered graph output. The editor
  recovery service may independently parse a neutral function fragment and represent a broken
  fragment as an unavailable recovery node with an attributed diagnostic.
- **`parse_expression`/`_parse_expression_impl`**: the *only* deliberate bare `except Exception` in
  the expression parser, converting any internal failure into an `"opaque"` `ParsedExpression`
  carrying the original source text. Documented in the function's docstring as an intentional,
  honest "could not statically understand this" signal, distinct from the value-computation paths.
- **`evaluate_expression`/`parse_expression_chain`**: Polars and sandbox exceptions on a computable
  expression (a malformed call such as `pl.col()`, Python `and`/`or`/`not` on an expression, an
  `UnsafeCodeError`) propagate to the trace/enrichment caller by design (see the high-level
  Failure model). `parse_expression_chain` catches only `SyntaxError` and converts it to an opaque
  singleton/empty chain. A failure is never replaced by `row_values.get(target_column)`.

## Testing

- `tests/test_safety.py` — discovers committed pipeline fixture files, parses
  each one, and asserts every resulting graph contains at least one Output
  node.

Tests live under `tests/`, split by concern:

- **`test_parser.py`** (~1300 lines) — end-to-end valid `.py` → `PipelineGraph` coverage: the strict
  path, syntax fail-loud behavior, submodel file parsing, the `flatten` parameter, decorator/config
  edge cases, malformed decorator kwargs, docstring stripping, preamble/preserved-block edge
  cases, roundtrip parsing, circular/non-existent/colliding/empty submodels, UTF-8 BOM handling.
- **`test_parser_submodels.py`** — `submodel_imports` and `extract_submodel_registrations`
  (the import form, every other spelling of the import refused at its line, the removed
  file-path form refused with the import form or with where the file belongs, an unused
  import, an unimported module, a module named like a built-in or a dunder, nested
  registrations), `parse_submodel_source`, `merge_submodels`, and cross-boundary-edge
  reconstruction.
- **`test_submodel_endpoint_properties.py`** — the generated graph/source family (ENG-T11):
  generated child definitions (an identity chain behind one public input and one public
  output port), one or two occurrences, and 1..4 authored parent connections drawn from the
  legal public-port forms and the private child-endpoint forms. Parsing conserves exactly
  the legal edges (occurrence instance ids with `in__`/`out__` handles) and rejects any
  private endpoint as dangling with the authored identities in authored order; flattening a
  fully wired legal graph yields one qualified runtime node per internal node per
  occurrence, no dangling edge, and every sink computes the generated source rows. The
  `hypothesis.find` negative control rewrites one legal connection into the private form and
  shows the acceptance flip naming exactly that connection. Consumers of an occurrence
  output name their parameter after the output's name (the sanitised public output
  port name from an owner; from an instance, the instance's name, or
  `<instance>_<port>` when the definition has several outputs), as codegen emits; any other parameter name is rejected
  unless explicitly bound through `inputMapping`.
- **`test_parser_conservation.py`** — regression tests asserting that parsing (and the implicit
  regeneration path) conserves source structure: boilerplate, docstrings, parameter buckets, node
  function shape, alias awareness, implicit-edge dedup, exact node/edge/handle/submodel identity,
  aggregated missing/unrecoverable submodel diagnostics, duplicate submodel-name rejection, and
  parity between editor recovery and strict authored-structure rules.
- **`TestPolarsParameterBinding`** (`tests/test_parser_conservation.py`) -- tests
  the strict parameter binding acceptance gate for Polars nodes: rejects unbound
  positional parameters and unconsumed incoming edges with exact diagnostic
  context; verifies that `inputMapping` correctly maps parameters, executes, and
  roundtrips through codegen; confirms consumers of submodel occurrence outputs
  bind the public port name; validates that parameter-mismatched pipelines
  load in degraded mode for editor recovery while rejecting saves with HTTP 409;
  and ensures definition nodes receiving public input ports bind to the sanitised
  port ID.
- **`test_parser_project_layout.py`**, **`test_parser_internals.py`**, and
  **`test_parser_sanitize_contracts.py`** — project-root/base-directory handling, internal
  parser invariants, and sanitisation contracts.
- **`test_parser_fail_loudly.py`** — a fail-loud sweep pinning config-path failures,
  submodel cross-boundary handle validation, extend-path staleness, and empty
  Polars code to *raise* rather than silently degrade.
- **`test_expression_parser.py`** (~1480 lines) — the primary TDD suite: arithmetic/unary
  operator precedence, `when`/`then`/`otherwise`, horizontal functions, `cast`/`fill_null`/
  `fill_nan`, numeric/string/datetime/list/struct namespace methods, window functions, method
  chaining, alias handling, multi-expression code blocks, opaque-pattern detection, syntax
  errors, special column names, and `EvaluatedExpression` value coverage across floats, ints,
  strings, nulls, NaN, infinities, dates, booleans, extreme numbers, conditionals, and horizontal
  functions. `TestTheLocatedExpressionAlone` pins a row-local column beside a window keeping its
  own type, text and columns, a window described from its own tree (alone and inside
  arithmetic), a scalar rebound after the formula read as the formula saw it (on separate lines
  and on one line), a binding keeping the preamble name it read when it ran, an augmented
  rebinding reported as `unresolved_name` rather than guessed, unary grouping (`-(x + z)`,
  nested, `-x ** 2`, `(-x) ** 2`, `~(a & b)`), a literal spelled like a column left unsubstituted,
  and a method named like a column keeping its name.
- **`test_expression_parser_advanced.py`** (~1100 lines) — real-world actuarial rating-formula
  scenarios (loss ratio, burn cost, frequency-severity, earned premium, NCD discount, IPT,
  reinsurance, age-band pricing, multi-line pricing, etc.), broader Polars method coverage
  (`is_between`/`is_in`/`is_null`/`replace`/`cut`/`qcut`/`rank`/`sample`/aggregations/
  `map_batches`/`concat_str`/`format`/`coalesce`), variable-chain and walrus/match-case/starred-
  expression handling, malformed/statically-unparseable code, and cross-node composition.
- **`test_expression_parser_coverage.py`** (~3150 lines, the largest file) — line/branch-coverage-
  driven unit tests that exercise individual converter/evaluator methods directly (namespace
  methods, `pl.col` dynamic resolution, `pl.lit` variants, chained `when`/`then`, reassignment
  chains, branch tracking, symbol-table internals). This is the file most likely to need touching
  whenever an internal helper's signature or dispatch shape changes.
- **`test_expression_parser_chain.py`** — `parse_expression_chain` dependency-walk scenarios plus
  division-by-zero/NaN/infinity/`None`-operator edge cases and window/nested-`when`/unicode-column
  evaluation.
- **`test_expression_parser_w3_fixes.py`** — regression tests pinning specific historical bug
  fixes from a past remediation pass.
- **`test_expression_parity_properties.py`** — the generated outcome property (ENG-T11): a bounded
  grammar (depth at most three) of documented, well-formed single-row forms — arithmetic,
  `abs`/`round`/`clip`/`fill_null`/`sqrt`/`log`, comparisons, `is_null`/`is_not_null`,
  `is_between` with every `closed` value, `is_in`, regex and literal `str.contains`,
  `to_lowercase`/`to_uppercase`, and `when/then/otherwise` — over int, float, tie-float and
  null cells, each example evaluated through `evaluate_expression` on a typed one-row frame and
  compared with Polars computing the same expression on that frame. It runs under the shared PR
  budget with retained curated edge examples and a `hypothesis.find` negative control proving a
  naive-Python evaluator (decimal `round`, substring `contains`, inclusive `is_between`, `False`
  for a null comparison) is caught on the same grammar. Classification pins assert that window,
  `shift`, `diff` and aggregation forms report `not_row_local` with no value, and the two-row
  window regression asserts that a `sum().over()` is never shown as its one-row value.

Strategy is unit + scenario-regression plus the generated single-row differential above.
Known coverage gaps:

- The neutral syntax-recovery scanner is exercised only against curated malformed-input scenarios, not
  randomly-generated broken Python source.
- The property covers documented single-row forms only; malformed calls and not-row-local forms
  are pinned by example, not generated.
- A multi-row sub-expression inside a formula (a condition comparing with a mean, say) is
  reported not computable; the trace does not re-evaluate it against its execution's frame.

## Syntax-invalid source

`src/haute/parser.py` wraps whole-file Python syntax failures as contextual `ParseError` values.
There is no textual syntax recovery: the editor's recovery loader (`src/haute/_pipeline_recovery.py`)
turns a syntax-invalid file into a `source_only` document carrying the Python syntax error's span,
and recovers nodes only from valid Python, resolving each known node inside an explicitly named
isolation boundary. Expected configuration/contract failures become stable editor diagnostics;
unexpected per-node failures are logged with an incident id. Editor recovery tests own the
end-to-end malformed-source behaviour.
