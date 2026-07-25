# Expression Parsing — High-Level Specification

## Purpose

This component covers two related, purely-static parsing jobs that both turn Python/Polars
source text into structured, human-consumable data without ever executing user code to
determine structure:

1. **Pipeline structural parsing** — turning a Haute pipeline `.py` file (written against the
   `@pipeline.<type>` decorator API) into the React Flow graph JSON the GUI renders, the
   executor consumes, and codegen writes back. This includes a regex-based recovery path for
   files with syntax errors, and resolution/merging of `pipeline.submodel(...)` references.
2. **Polars expression parsing** — turning the Polars `with_columns()` expression code inside a
   single pipeline node into a human-readable formula string, and optionally evaluating that
   formula against concrete row values to reproduce Polars' computed result for the trace/debug
   UI.

Both exist because the GUI, trace viewer, and codegen round-trip all need a faithful, structural
understanding of user-authored pipeline code without actually running it (structural parsing) or
while re-deriving what a single already-executed step computed and why (expression parsing).

## Scope

In scope:
- `.py` source → `PipelineGraph` (nodes, edges, metadata, preamble, preserved blocks) via
  `ast`, with a regex/partial-AST fallback when the whole file fails `ast.parse`.
- Resolving and merging `pipeline.submodel("path")` references into the parent graph, either
  hierarchically (collapsed placeholder nodes) or flattened.
- Polars `with_columns()`/`select()` expression AST → human-readable formula text, referenced
  columns, and literal constants.
- Substituting concrete row values into a parsed formula and computing a concrete result that
  mirrors Polars' runtime semantics (null propagation, Kleene logic, overflow, rounding, etc.).
- Walking backward through same-node `with_columns()` calls to build a column's dependency chain.

Out of scope (owned by neighbouring components, cross-linked below):
- Writing pipeline files back to disk / formatting-preserving edits — [codegen](../codegen/high-level.md).
- AST utility primitives (`_ast_helpers`) and user-code extraction (`_code_extraction`) that this
  component's `parser.py` and `_parser_regex.py` call into — owned by
  [codegen](../codegen/high-level.md).
- Config-dict construction (`_config_builder`) and conversion of parsed node/edge data into
  `GraphNode`/`GraphEdge` models (`_graph_builders`) — owned by
  [pipeline-config](../pipeline-config/high-level.md).
- The submodel placeholder/port-classification and flatten algorithms themselves
  (`_flatten.py`, `_submodel_graph.py`) — [submodels](../submodels/high-level.md); this component
  only decides *when* to invoke them and supplies the parsed child graphs.
- Project-root resolution and config file I/O (`_project.py`, `_config_io.py`) —
  [pipeline-config](../pipeline-config/high-level.md).
- Sandboxed execution of user code at runtime (`_sandbox.py`) —
  [sandbox-security](../sandbox-security/high-level.md). Expression evaluation in this component
  never uses `eval`/`exec`; it is a hand-written AST interpreter over a constrained grammar, not a
  sandboxed general-purpose executor.

## Behaviour

**Structural parsing**
- `parse_pipeline_file`/`parse_pipeline_source` use regex-based partial recovery after a
  whole-file `SyntaxError`, allowing the GUI to render recoverable nodes and edges. Recovery is
  deliberately conservative: malformed constructs the fallback can locate but cannot safely
  reconstruct raise `ParseError` rather than returning a plausible-but-incomplete graph.
- Missing, unreadable, invalid, or schema-invalid config sidecars raise `ConfigError` on the
  healthy AST path. In regex recovery, a syntactically broken individual function body is kept as
  a node carrying `config["_load_error"]`; the fallback graph's warning remains the file-level
  syntax-recovery message. The healthy parser's load-error warning aggregator only reports nodes
  already carrying `_load_error`; normal sidecar load failures do not enter that path.
- A referenced submodel file is parsed atomically. If its module AST is invalid, parsing raises
  `ParseError` naming the child file and syntax location rather than merging an empty warning
  graph that has lost every child node and edge.
- `pipeline.submodel(...)` references are resolved relative to the project root / pipeline
  directory. Every authored reference must resolve to a readable file. Missing files raise one
  `ParseError` that lists every unresolved path instead of returning a graph with those
  submodels omitted.
- An in-memory `parse_pipeline_source` call that contains submodel references must supply
  `_base_dir` or `_submodel_base_dir`. Without a resolution root the parser raises `ParseError`
  with every unresolved authored path; returning only the root nodes would violate the same
  conservation contract as a missing file.
- Referencing the same resolved submodel file more than once raises a dedicated `ParseError`
  naming that file and the authored references. This is distinct from two different files
  declaring the same `Submodel(...)` name.
- Two different submodel files may not declare the same `Submodel(...)` name. A collision raises
  `ParseError` naming the shared pipeline name and every involved file; no file wins by load
  order.
- `flatten=True` dissolves nested submodel graphs into one flat graph (for the executor, trace,
  and deploy); the default `False` keeps hierarchical `submodel__<name>` placeholder nodes so the
  GUI can render a collapsed/expandable submodel box.
- Parameter-name inference spans a submodel boundary after every child has been loaded. If a root
  or child function parameter names a node in the parent or another loaded child graph, the parser
  constructs the same implicit edge it would have constructed within one file; hierarchical and
  flattened results preserve it.
- Nested submodels (a submodel file itself calling `pipeline.submodel(...)`) are capped at one
  level. Parsing raises `ParseError` naming the containing file and every nested path, because
  returning the outer graph while omitting the nested references would not conserve authored
  structure.
- The regex fallback recovers everything it can locate and unambiguously reconstruct, and fails
  loud for content it can see but cannot safely recover (an unclosed `connect()` call, a
  non-literal decorator keyword argument, an `async def` pipeline node, a config sidecar folder
  present without a matching `config=` kwarg, or an unrecoverable `pipeline.submodel(...)`
  reference) rather than silently dropping or guessing at it. When several submodel references
  are unrecoverable, the single error reports all of them.
- Preamble boundaries recognise both module aliases (`import haute as ht`) and direct constructor
  aliases (`from haute import Pipeline as BuildPipeline`), including multiline pipeline
  construction. The syntax-error fallback applies the same alias boundary rules textually.
- Before either parser path returns, a structure-conservation gate checks the authored node ids,
  explicit edge endpoints and handles, implicit parameter edges, and submodel references against
  the constructed graph. The gate permits a parent edge endpoint to name a loaded submodel child,
  but rejects every other dangling endpoint with an actionable `ParseError`. Exact duplicate
  `connect()` identities have their own diagnostic rather than being reported as a generic
  conservation mismatch.

**Expression parsing**
- `parse_expression(code, target_column)` statically locates the `with_columns()`/`select()`
  call that produces `target_column` (last-match-wins across chained/sequential calls) and
  converts its AST into a human-readable formula string, plus the list of referenced columns and
  literal constants it contains. It never raises: code it cannot statically understand — lambdas,
  UDFs, values assigned inside `if`/`for`/`try`/`with`/`match`, or anything that fails to parse —
  becomes an `"opaque"` result carrying the original source text, which is the honest "could not
  understand this" signal rather than a guessed value.
- `evaluate_expression(code, target_column, row_values, ...)` additionally substitutes concrete
  column values into the formula text and computes a concrete result by walking the AST with a
  hand-written interpreter for a constrained Polars subset, tuned to Polars' runtime semantics
  rather than Python's where implemented: null
  propagates through arithmetic and comparisons; `&`/`|` use Kleene three-valued logic when either
  side is boolean or both are null; division/floor-division/modulo by zero yield `±inf`/`nan`
  (float) or `null` (int) instead of raising; an integer result outside the signed-64-bit range is
  reported as uncomputable (`None`) rather than a wrong wraparound value; `.round()` matches
  Polars' float-scale-then-half-to-even rounding, not Python's decimal-accurate `round()`; a
  negative base raised to a non-integer float exponent is `NaN`, matching Polars' float domain.
  Unsupported AST nodes and methods commonly return `None`; several malformed-call guards also
  return `None`, ignore unknown arguments, or retain an input value rather than reproducing
  Polars' exception. For supported operations, an internal evaluator failure **propagates**
  rather than being swallowed — see Failure model.
- For `pl.when()/.then()/.otherwise()` conditionals, evaluation additionally reports which branch
  was actually taken (and, for nested conditionals, which branch was taken at each inner level),
  so the trace UI can highlight the live branch and dim the others.
- `parse_expression_chain(code, target_column)` walks backward through the same node's
  `with_columns()` calls to build the transitive dependency chain feeding `target_column` — every
  intermediate column referenced along the way, in dependency order (earliest first). A syntax
  error is converted into a one-element opaque chain (or `[]`), while non-syntax internal failures
  are not caught.

## Design rationale

- The primary structural parser uses Python's `ast` module because pipeline files are executable
  Python and this path only needs structural reads. Codegen is a separate text-generation path:
  it currently uses source templates plus `tokenize`-aware rewrites, not this parser's AST and not
  a LibCST write-back implementation.
- The regex fallback exists so that a single syntax error anywhere in a large pipeline file does
  not blank out the entire GUI graph — the user can see and fix the one broken node while
  everything else keeps rendering. Recovered call/decorator *sites* are found textually (the file
  is unparseable by definition), but recovered *fragments* are re-parsed with the real `ast`
  module wherever possible, so both parse paths agree on decorator-kwarg types, connect-call
  shapes, and submodel paths. The trade-off is conservatism: anything visible-but-ambiguous fails
  loud instead of being guessed at, consistent with this codebase's fail-loud-over-silent-fallback
  policy.
- Expression evaluation deliberately hand-rolls interpretation of a constrained AST subset rather
  than using `eval`/`exec`. This avoids executing arbitrary user code to answer a display
  question, and lets the evaluator intentionally diverge from Python semantics wherever Polars'
  own semantics differ (null propagation, Kleene logic, dtype-driven overflow/div-by-zero
  behaviour).
- The evaluator is explanatory, not a complete Polars engine. The parity suite directly compares
  a curated set of operations and values against the pinned Polars runtime; it does not establish
  parity for every namespace method, dtype, malformed call, or window operation. Returning `None`
  is the current "unsupported/uncomputable" result for many of those gaps.
- `evaluate_expression` used to fall back to the pipeline's actually-observed output value
  (`row_values.get(target_column)`) whenever the evaluator raised. That fallback was removed: it
  laundered evaluator bugs into a result that looked self-consistent with the trace, hiding
  exactly the divergence a developer would need to see to fix the evaluator. The current behaviour
  — propagate the exception — is a direct instance of this codebase's "fail loud, never guess"
  principle (see the project's `CLAUDE.md`).
- The module-level `_cached_parse` (an `lru_cache` over `ast.parse`) exists because
  `parse_expression`, `_compute_result`, `_evaluate_conditional_branches`, and
  `parse_expression_chain` each reparse the *same* code string for the same node; caching is safe
  because every consumer only reads the cached tree — substitution/reassignment-chain resolution
  build new AST nodes rather than mutating it in place.

## Interactions

- Depends on [pipeline-config](../pipeline-config/high-level.md) for project-root inference and
  config file loading (`_project.get_project_root`, `_config_io.find_config_by_func_name`,
  `_config_io.has_config_folder`).
- Depends on [codegen](../codegen/high-level.md) for the AST/source utility and user-code
  extraction primitives shared by generation and parsing.
- Depends on [pipeline-config](../pipeline-config/high-level.md) for config-dict construction and
  the node/edge builders that both `parser.py` (healthy path) and `_parser_regex.py` (fallback
  path) import directly, so both paths produce identically-shaped `GraphNode`/`GraphEdge`
  objects.
- Depends on and cooperates with [submodels](../submodels/high-level.md): this component decides
  when a `pipeline.submodel(...)` reference must be resolved and parsed, but the placeholder-node
  construction, boundary-port classification, and flatten algorithm live there.
- `PipelineGraph`, the output type, is the canonical structure shared with the executor, codegen,
  and the server API layer — changes here are visible everywhere a pipeline is rendered or run.
- The expression-parsing half is consumed by the trace/execution-engine enrichment layer, which
  attaches `ParsedExpression`/`EvaluatedExpression` to each executed step for the trace UI.
- Contrast with [sandbox-security](../sandbox-security/high-level.md): that component actually
  executes user pipeline code in a restricted runtime; this component never executes user code —
  `evaluate_expression`'s "computation" is a from-scratch AST interpreter, not a sandboxed `exec`.

## Failure model

- Structural parsing never raises `SyntaxError` to the caller — an unparseable file is always
  handled by the regex fallback, which still returns a `PipelineGraph` (with a warning noting the
  syntax error's line).
- Structural parsing *does* raise `ParseError` for content the fallback scanner can see but cannot
  safely reconstruct: an unclosed `connect()`/`submodel()`/decorator argument list, a non-literal
  decorator keyword argument or (on the healthy path) a non-literal `submodel()` path, an
  `async def` pipeline node, a config sidecar folder with no `config=` kwarg, a missing submodel
  file, a submodel reference without a resolution root, the same resolved submodel file referenced
  more than once, two different submodel files declaring the same name, a nested submodel
  reference, an exact duplicate edge identity, or any node/edge/handle identity rejected by the
  conservation gate. A parse that silently returned a plausible-but-incomplete graph here would
  corrupt the file on the next save, which this codebase treats as strictly worse than a loud
  failure.
- Config sidecar load/validation failures are raised as `ConfigError`. `_load_error` is used by
  regex recovery for a function body fragment that cannot be parsed, not as the normal sidecar
  failure transport.
- `parse_expression` never raises: any internal exception becomes an `"opaque"` result. This is
  the single deliberate catch-all in the expression parser, justified as the honest "could not
  statically understand this" signal.
- `evaluate_expression` does not catch evaluator failures, and `parse_expression_chain` does not
  catch non-syntax internal failures; they propagate to the trace/enrichment caller, which is
  expected to record a visible error marker. `parse_expression_chain` does catch `SyntaxError` and
  returns the opaque `parse_expression` result as a singleton when available.
