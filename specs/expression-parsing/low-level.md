# Expression Parsing — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/parser.py` | Strict public entry points `parse_pipeline_file` / `parse_submodel_file` / `parse_pipeline_source`. Orchestrates AST metadata/node/edge extraction, submodel resolution + merge, conservation, and graph-shape validation for valid pipeline source; whole-file syntax errors raise contextual `ParseError`. |
| `src/haute/_parser_conservation.py` | Strict fail-loud acceptance gate. Verifies that parsed root node IDs, ordered edge/handle identities, submodel references, and cross-boundary endpoints conserve the authored structure; also builds the deterministic missing-submodel diagnostic. |
| `src/haute/_parser_regex.py` | Neutral syntax-recovery discovery. `recover_pipeline_fragments` locates pipeline metadata, `@pipeline.<type>` function fragments, `pipeline.connect()` declarations, and `pipeline.submodel()` registrations textually, re-parsing individual fragments with `ast` where possible. It never constructs canonical graph models. |
| `src/haute/_parser_submodels.py` | `extract_submodel_registrations` / `parse_submodel_source` / `merge_submodels`: resolves explicit `pipeline.submodel("path", ...)` registrations, parses each referenced submodel file into its own `PipelineGraph`, and merges canonical occurrences into the parent (hierarchical or flattened). |
| `src/haute/_expression_parser.py` | `parse_expression` / `evaluate_expression` / `parse_expression_chain` and their supporting classes: AST-based conversion of a Polars with-columns expression to human-readable text (`_ExprConverter`) and to a concrete, Polars-mirroring value (`_ExprEvaluator` / `_BranchTrackingEvaluator`). |

## Key types and data structures

- **`ParsedExpression`** (dataclass, `_expression_parser.py`) — `target_column`, `expression_text`,
  `expression_type`
  (`"arithmetic"|"conditional"|"horizontal_func"|"function_call"|"window"|"opaque"`),
  `referenced_columns: list[str]`, `constants: list[Any]`, `sub_expressions: list[ParsedExpression]`
  (nested conditionals inside a `then`/`otherwise` arm), `source_line: int | None`.
- **`EvaluatedExpression`** (dataclass, extends `ParsedExpression`) — adds `substituted_text`,
  `result_value`, `input_values: dict[str, Any]`, and conditional-branch metadata
  (`taken_branch`, `taken_branch_index`, `dimmed_branches: list[int]`,
  `nested_branches: list[str]`).
- **`PipelineGraph`** (`src/haute/_types.py`, owned by
  [server-api](../server-api/low-level.md) and produced here) — `nodes`,
  `edges`, `pipeline_name`, `pipeline_description`, `preamble`,
  `preserved_blocks`, `source_file`, `source_revision` (server-populated
  live-document metadata), `warning`, `submodels`. Canonical structure shared
  with the executor, codegen, deploy, and the
  server API layer.
- **`_ExprConverter`** (`_expression_parser.py`) — one AST-node-type-dispatch method per handled
  node kind; accumulates `columns`, `constants`, `expr_type`, `sub_expressions`, and an
  `_is_opaque` flag as it walks. Takes an optional `symbol_table` for top-level variable
  resolution and a `_resolving` set that guards against infinite recursion on self-referential
  names.
- **`_ExprEvaluator`** (`_expression_parser.py`) — mirrors `_ExprConverter`'s dispatch shape but
  computes a concrete value instead of text, given `row_values` and the same `symbol_table`.
  `_BranchTrackingEvaluator` subclasses it, overriding `_eval_clauses`/`_take_branch` to additionally
  record which `when`/`then`/`otherwise` branch fired at each nesting level.
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
decorated nodes / connect edges / preamble / preserved blocks by
importing their implementation modules directly →
collect labels from any nodes already carrying `_load_error` into a graph-level `warning` → if any
`pipeline.submodel()` calls were found, require `_base_dir` or `_submodel_base_dir` (otherwise
raise with every unresolved authored path), resolve registrations, group repeated paths by canonical definition id, parse
each definition file once, and call `_parser_submodels.merge_submodels` → run
the structure-conservation gate → `validate_pipeline_graph_shape_contracts` (owned outside this
component) → log `pipeline_parsed`.

**`recover_pipeline_fragments`** (`_parser_regex.py`): recover alias-aware pipeline metadata by
parsing otherwise-valid import lines independently, then locate the matching constructor with a
balanced-parenthesis scan → find every `@pipeline.<type>` block textually, retaining the decorator,
function identity/signature/body, parameters, and source lines → re-parse individual decorator,
connection, and submodel call fragments with `ast` wherever possible → recover preamble and
`# haute:preserve` blocks with line-oriented scans → return one frozen neutral fragment document.
No step resolves node configuration, builds canonical nodes/edges, merges submodels, or returns a
`PipelineGraph`; `src/haute/_pipeline_recovery.py` is the sole orchestrator that may resolve the
fragments into editor-only recovery DTOs.

**`merge_submodels`** (`_parser_submodels.py`): validate that every parsed
child's declared `definition_id` matches its registration and that literal
structured input/output ports are present. Build one `SubmodelDefinition` per
definition id and one `SUBMODEL` occurrence per registration; each occurrence
uses its explicit immutable `instance_id`, alias, optional label, and config
`{definitionId, alias}`. Parent connect endpoints use aliases and declared
public port ids, which become `in__<portId>`/`out__<portId>` graph handles;
internal child ids are never accepted as parent endpoints. Only when
`flatten=True` is the canonical hierarchical graph passed to
`flatten_graph`. A submodel file containing its own registration raises
`ParseError`, preserving the one-level nesting contract.

**`parse_expression`** (`_expression_parser.py`): strip a leading BOM → `_cached_parse(code)` →
bail to `_opaque(...)` on empty input or `SyntaxError` → `_has_control_flow_wrapping_target`: if
the `with_columns()` producing `target_column` sits inside `if`/`for`/`while`/`try`/`with`/`match`,
bail to opaque (the value is branch-dependent and cannot be statically resolved) →
`_build_safe_symbol_table` collects only *top-level* simple `Name = expr` assignments (control-flow
bodies are excluded) → `_find_control_flow_assigned_vars` separately records names assigned
*inside* control flow, so an expression referencing one of those is poisoned even if the same name
also has an unrelated top-level binding → `_resolve_reassignment_chains` folds sequential
reassignment patterns (`expr = pl.col("base"); expr = expr * pl.col("f")`) into the symbol table by
substituting known names into each new value's AST as it's processed, in statement order →
`_find_with_columns_calls` locates every `with_columns()`/`select()` call, ordered by (line, col,
end_line, end_col) source span so a chained `df.with_columns(A).with_columns(B)` — which
`ast.walk`'s breadth-first parent-before-child order would otherwise visit outer-call-first —
comes back in true execution order → scan all calls, "last alias match wins", with two fallback
scans (dynamic alias resolved through the symbol table; then no-alias expressions matched by
`_infer_auto_name`) if the direct alias scan finds nothing → convert the winning expression AST
with `_ExprConverter`.

**`evaluate_expression`** (`_expression_parser.py`): wrap bare dot-chain code (`.method(...)`),
including snippets with leading indentation, or a naked expression into a synthetic `df = (...)`
statement through the same wrapper used by `parse_expression_chain` so both APIs parse the same
source shape → merge
`preamble_ns` constants under `row_values` (column values win on key collision) → detect a window
function via the substring `".over("` → `parse_expression` for the text/columns/type → for windows,
`_add_window_partition_cols` regex-extracts `.over('col')` partition columns and
`_build_window_description` regex-extracts the aggregation function/column/partition names into a
canned `"{agg} of {col} over {partition}"` string → `_substitute_values` builds the substituted
formula text (see Edge cases) → resolve any still-unresolved preamble constant names by literal
text replacement → `_compute_result` reparses and evaluates the winning AST node with a fresh
`_ExprEvaluator` → for `expression_type == "conditional"`, additionally run
`_evaluate_conditional_branches` with a `_BranchTrackingEvaluator` to populate the branch-tracking
fields.

**`parse_expression_chain`** (`_expression_parser.py`): strip/wrap the code and parse it; a
`SyntaxError` is converted to `[parse_expression(...)]` when that opaque result exists (otherwise
`[]`) → parse every column definition across all
`with_columns()` calls exactly once into `parsed_by_col`/`refs_by_col` maps (reusing one shared
tree/converter pass rather than re-invoking `parse_expression` per chain element) → if
`target_column` was not defined anywhere, return `[]` → depth-first walk backward from
`target_column` through `refs_by_col`, appending each column post-order (so a dependency is
appended before the column that depends on it) → return the parsed expressions in that order.

## Edge cases and invariants

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
  name also happens to have an (irrelevant) top-level binding elsewhere.
- **Signed 64-bit integer overflow**: `_ExprEvaluator._binop` reports an integer result outside
  `[-2**63, 2**63-1]` as `None` rather than a Python-bigint value, because the evaluator is
  dtype-unaware and cannot know whether the real Polars column is `Int8`/`Int32`/`Int64`/`UInt64`,
  so it refuses to guess a wraparound width.
- **Division/modulo by zero**: mirrors Polars, not Python — float `x/0` → `copysign(inf, x)`
  (`0/0` → `nan`); integer `//`/`%` by zero → `None` (Polars null); float `%0` → `nan`. Never
  raises `ZeroDivisionError`.
- **Kleene three-valued `&`/`|`**: only engages when at least one operand is a concrete `bool` or
  both are `None` (`_is_bool_kleene_operand`); this must run *before* the generic "any operand is
  null → null" short-circuit, because `False & null` is `False` and `True | null` is `True` in
  Polars, not null. Plain integer bitwise `&`/`|` falls through unaffected.
- **`round()` divergence from Python**: matches Polars' `round(v * 10**n) / 10**n` on the f64 value
  with half-to-even tie-breaking, which is *not* the same as Python's decimal-accurate
  `round(v, n)` (e.g. `round(2.675, 2)` is `2.68` under this evaluator/Polars 1.39 but `2.67` under
  bare Python `round`). Pinned by `tests/test_expression_parser_polars_parity.py`.
- **`pow()` with a negative base and non-integer float exponent** → `NaN`, matching Polars' float
  domain (Python would return a `complex`).
- **Single-pass value substitution**: `_substitute_values` builds one combined word-boundary regex
  over all identifier-like column names, longest-first, and substitutes in one left-to-right pass —
  so a value inserted for one column can never be re-scanned and corrupted by a shorter column
  name's pattern matching inside the inserted text. Non-identifier column names (spaces/special
  characters) fall back to literal (non-regex) replacement.
- **BOM handling**: `parse_expression`, `parse_expression_chain`, `_compute_result_impl`, and
  `_evaluate_conditional_branches` all strip a leading `﻿` before parsing (`evaluate_expression`
  inherits this only transitively, by calling into `parse_expression`/`_compute_result_impl`).
- **Neutral-recovery string/comment-aware scanning**: `_skip_string_literal` deliberately avoids
  `tokenize` (the file is syntactically broken by definition) and, when a triple-quoted string
  never closes, skips to EOF — conservative, because everything after an unclosed triple-quote is
  part of the broken string from Python's perspective. `_position_is_code` /
  `_iter_top_level_anchor_matches` use the same scanner so decorator/connect/submodel anchors
  inside comments or string literals are never mistaken for real code.
- **Statement-wrapper detection**: `_parenthesized_wrapper_depth_before` distinguishes a pure
  `(\npipeline.connect(...)\n)` parenthesised statement (still top-level, should behave like the
  healthy AST parser) from an assignment/list/dict continuation like
  `disabled = [\npipeline.connect(...)\n]` (must stay rejected — it is not a top-level call).
- **Preamble boundaries are alias-aware**: a valid module uses AST statement line spans to stop
  before an aliased or multiline `Pipeline(...)` construction; neutral syntax recovery parses
  recoverable import lines independently, including comma-separated names and trailing comments,
  so both `import haute as <alias>` and `from haute import Pipeline as <alias>` preserve the same
  user preamble without capturing the constructor.
- **Nested submodels capped at one level**: a submodel file's own
  `pipeline.submodel(...)` calls raise `ParseError` as a group; they are never recursed into or
  omitted from an otherwise healthy-looking graph.
- **Missing submodel files are aggregated**: all resolved paths are checked before merge. One or
  more missing files produce one `ParseError` with deterministic `missing_paths` detail in
  authored order.
- **Submodel resolution roots are mandatory**: healthy in-memory parsing cannot conserve a
  `pipeline.submodel()` reference without `_base_dir` or `_submodel_base_dir`, so it raises with
  `unresolved_paths` instead of returning a root-only graph. Editor recovery resolves submodel
  fragments against the explicitly supplied project and parent-pipeline roots.
- **Repeated definition files are intentional for reusable occurrences**:
  registrations resolving to one file are grouped and parsed once. They must
  agree on one explicit definition id.
- **Definition/file identity is one-to-one**: one definition id resolving to
  multiple files, conflicting definition ids for one file, or a child whose
  declared id differs from the parent registration raises `ParseError`.
- **Occurrence identity is explicit**: missing/non-literal/blank
  `definition_id`, `instance_id`, or `alias` fields, and duplicate instance ids
  or aliases, fail before merge. No file/name/node-id inference is attempted.
- **Public boundary connections are explicit**: a parent `connect` endpoint
  that names an occurrence alias must also name a declared public port id.
  Function-parameter inference remains inside its owning root or definition
  graph; it never reaches through a definition interface or exposes an
  internal child id.
- **Conservation is an acceptance gate**: the parser compares authored root
  nodes and ordered edges, registration paths and explicit identity fields,
  occurrence aliases and public port ids, and the constructed
  definition/occurrence registry. Each child source is conserved within its
  own definition graph. Exact duplicate `connect()` identities raise the
  dedicated diagnostic; every other difference raises `ParseError` before
  graph-shape validation or return.
## Error handling

- **`ConfigError`** propagates from `src/haute/_config_builder.py` when a healthy parse cannot
  load/validate a referenced sidecar or a folder-backed node omits `config=`. These failures are
  not converted to `_load_error` nodes or graph warnings.
- **`ParseError`** (raised, not caught, by this component) for: an unclosed
  `pipeline.connect()`/`pipeline.submodel()`/`Pipeline(...)`/decorator-argument-list scan
  (`_scan_call_end` exhausts the source without balancing); a decorator/submodel/metadata call
  with trailing text after its closing paren; a decorator keyword argument or
  `pipeline.submodel()` path that is not a literal (only
  Python literals and the sanctioned `Contract(...)` constructor are resolvable at parse time);
  `**kwargs` expansion in a decorator; an `async def` pipeline node
  function; a syntactically invalid referenced submodel file; a missing submodel file; a
  submodel reference without a resolution root; a conflicting definition/file registration; a missing or duplicate
  canonical occurrence identity; an invalid structured public-port contract; nested submodel references; an exact duplicate
  edge identity; a structure-conservation mismatch; or a submodel path that escapes the project
  root. A folder-backed node with no matching `config=` kwarg raises `ConfigError` through
  `_sidecar_required_error`, consistently with other sidecar configuration failures.
- **`SyntaxError` from `ast.parse`** at the top of `parse_pipeline_source` is converted into a
  contextual `ParseError`; strict callers never receive syntax-recovered graph output. The editor
  recovery service may independently parse a neutral function fragment and represent a broken
  fragment as an unavailable recovery node with an attributed diagnostic.
- **`parse_expression`/`_parse_expression_impl`**: the *only* deliberate bare `except Exception` in
  the expression parser, converting any internal failure into an `"opaque"` `ParsedExpression`
  carrying the original source text. Documented in the function's docstring as an intentional,
  honest "could not statically understand this" signal, distinct from the value-computation paths.
- **`evaluate_expression`/`parse_expression_chain`**: evaluator/non-syntax internal exceptions
  propagate to the trace/enrichment caller by design (see the high-level Failure model).
  `parse_expression_chain` catches only `SyntaxError` and converts it to an opaque singleton/empty
  chain. The
  removed prior behaviour — silently falling back to
  `row_values.get(target_column)` — is documented in the module as deliberately deleted because it
  laundered evaluator bugs into a self-consistent-looking trace.
- **`ValueError`** raised (not caught) from `_ExprEvaluator._eval_concat_str` for a non-`str`
  `separator` or non-`bool` `ignore_nulls` keyword, and from `_eval_replace` for an incomplete
  `replace_strict` mapping with no `default=` — mirroring Polars' own `InvalidOperationError`
  behaviour rather than silently coercing or leaving the value unmapped.

## Testing

- `tests/test_safety.py` — discovers committed pipeline fixture files, parses
  each one, and asserts every resulting graph contains at least one Output
  node.

Tests live under `tests/`, split by concern:

- **`test_parser.py`** (~1300 lines) — end-to-end valid `.py` → `PipelineGraph` coverage: the strict
  path, syntax fail-loud behavior, submodel file parsing, the `flatten` parameter, decorator/config
  edge cases, malformed decorator kwargs, docstring stripping, preamble/preserved-block edge
  cases, roundtrip parsing, circular/non-existent/colliding/empty submodels, UTF-8 BOM handling.
- **`test_parser_regex.py`** — unit tests for neutral regex-recovery fragments and scanners,
  including function, decorator-argument, connection, submodel, string, and comment boundaries.
- **`test_parser_regex_contracts.py`** / **`test_parser_regex_ast_kwargs.py`** — pin the exact
  decorator-kwarg value-parsing policy (literals + `Contract(...)` only, everything else fails
  loud) across scalar/compound/multiple/degenerate/invalid-syntax cases; a dedicated TDD gate
  regression-tests one specific historical codebase-review finding.
- **`test_parser_submodels.py`** — `extract_submodel_registrations`, `parse_submodel_source`,
  `merge_submodels`, and cross-boundary-edge reconstruction.
- **`test_parser_conservation.py`** — regression tests asserting that parsing (and the implicit
  regeneration path) conserves source structure: boilerplate, docstrings, parameter buckets, node
  function shape, alias awareness, implicit-edge dedup, exact node/edge/handle/submodel identity,
  aggregated missing/unrecoverable submodel diagnostics, duplicate submodel-name rejection, and
  parity between neutral fragment discovery/editor recovery and strict authored-structure rules.
- **`test_parser_project_layout.py`**, **`test_parser_internals.py`**, and
  **`test_parser_sanitize_contracts.py`** — project-root/base-directory handling, internal
  parser invariants, and sanitisation contracts.
- **`test_parser_fail_loudly.py`** — a fail-loud sweep pinning config-path failures, stale
  instance mapping, submodel cross-boundary handle validation, extend-path staleness, and empty
  Polars code to *raise* rather than silently degrade.
- **`test_expression_parser.py`** (~1480 lines) — the primary TDD suite: arithmetic/unary
  operator precedence, `when`/`then`/`otherwise`, horizontal functions, `cast`/`fill_null`/
  `fill_nan`, numeric/string/datetime/list/struct namespace methods, window functions, method
  chaining, alias handling, multi-expression code blocks, opaque-pattern detection, syntax
  errors, special column names, and `EvaluatedExpression` value coverage across floats, ints,
  strings, nulls, NaN, infinities, dates, booleans, extreme numbers, conditionals, and horizontal
  functions.
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
- **`test_expression_parser_polars_parity.py`** — value-asserting tests that cross-check
  `_ExprEvaluator`'s output against real Polars computations; the source of truth for the
  documented `round()`-half-to-even and similar intentional divergences from naive Python
  semantics.

Strategy is unit + scenario-regression throughout; no property-based/fuzz testing was found.
Known coverage gaps:

- The neutral syntax-recovery scanner is exercised only against curated malformed-input scenarios, not
  randomly-generated broken Python source.
- `test_expression_parser_polars_parity.py` cross-checks a curated subset (notably rounding,
  regex `str.contains`, `is_between`, `is_in`, conditionals, and a few numeric methods) against
  real Polars. It is not an exhaustive semantic differential. Unsupported AST/method forms
  return `None`, and defensive malformed-call branches sometimes intentionally differ from
  Polars by ignoring an argument or avoiding an exception.
- Window result calculation is described textually using regex extraction and a row-local AST
  evaluator; the suite does not prove full partition/window semantics against a multi-row Polars
  frame.

## Neutral syntax-recovery fragments

`src/haute/_parser_regex.py` owns textual discovery primitives and
`recover_pipeline_fragments(source)`. Its public recovery result is a frozen neutral fragment
model and contains no `GraphNode`, `GraphEdge`, or `PipelineGraph`. No canonical syntax-recovery
integration exists. `src/haute/parser.py` never imports this module and wraps
whole-file Python syntax failures as contextual `ParseError` values.

`src/haute/_pipeline_recovery.py` may parse decorator fragments and resolve each known node inside
an explicitly named isolation boundary. Expected configuration/contract failures become stable
editor diagnostics; unexpected per-node failures are logged with an incident id. Tests keep the
textual scanners focused on conservation and fail-loud ambiguity, while editor recovery tests own
the end-to-end malformed-source graph behaviour.
