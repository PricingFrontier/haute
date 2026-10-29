# Expression Parsing — High-Level Specification

## Purpose

This component covers two related parsing jobs that both turn Python/Polars source text into
structured, human-consumable data without executing user code to determine structure:

1. **Pipeline structural parsing** — strictly turning a valid Haute pipeline `.py` file (written
   against the `@pipeline.<type>` decorator API) into the canonical `PipelineGraph` consumed by
   execution, codegen, deploy, and strict APIs. The node and connection skeletons it reads from
   the syntax tree also serve the editor-only recovery service, which never produces a
   canonical graph.
2. **Polars expression parsing** — turning the Polars `with_columns()` expression code inside a
   single pipeline node into a human-readable formula string, and optionally having Polars
   compute that formula's value on one traced row for the trace/debug UI.

Both exist because the GUI, trace viewer, and codegen round-trip all need a faithful, structural
understanding of user-authored pipeline code without actually running it (structural parsing) or
while re-deriving what a single already-executed step computed and why (expression parsing).

## Scope

In scope:
- Valid `.py` source → `PipelineGraph` (nodes, edges, metadata, preamble, preserved blocks) via
  `ast`; whole-file syntax errors raise contextual `ParseError`.
- A valid file the strict parser refuses → node and connection skeletons for the separate editor
  recovery service; canonical graph consumers never accept what it recovers. Syntax-invalid
  source has no skeletons.
- Reading a pipeline file's submodel imports (`from modules import <name>`, each naming the
  file `modules/<name>.py`) and its `pipeline.submodel(<name>.submodel, "<occurrence>")`
  registrations, and resolving and merging the definitions they name into the parent graph,
  either hierarchically (collapsed occurrence nodes) or flattened.
- Polars `with_columns()`/`select()` expression AST → human-readable formula text, referenced
  columns, and literal constants.
- Substituting concrete row values into a parsed formula, and having Polars compute its result on
  the traced row wherever one row determines it.
- Walking backward through same-node `with_columns()` calls to build a column's dependency chain.

Out of scope (owned by neighbouring components, cross-linked below):
- Writing pipeline files back to disk / formatting-preserving edits — [codegen](../codegen/high-level.md).
- AST utility primitives (`_ast_helpers`) and user-code extraction (`_code_extraction`) that this
  component's `parser.py` calls into — owned by
  [codegen](../codegen/high-level.md).
- Config-dict construction (`_config_builder`) and conversion of parsed node/edge data into
  `GraphNode`/`GraphEdge` models (`_graph_builders`) — owned by
  [pipeline-config](../pipeline-config/high-level.md).
- The submodel occurrence/port-classification and flatten algorithms themselves
  (`_flatten.py`, `_submodel_instances.py`) — [submodels](../submodels/high-level.md); this component
  only decides *when* to invoke them and supplies the parsed child graphs.
- Project-root resolution and config file I/O (`_project.py`, `_config_io.py`) —
  [pipeline-config](../pipeline-config/high-level.md).
- Sandboxed execution of user code at runtime (`_sandbox.py`) —
  [sandbox-security](../sandbox-security/high-level.md). Expression evaluation builds the located
  expression through that component's code validator and restricted globals, the boundary node
  code itself runs under, and lets Polars evaluate it.

## Behaviour

**Structural parsing**
- `parse_pipeline_file`/`parse_pipeline_source` are strict. A whole-file `SyntaxError` becomes a
  contextual `ParseError`; neither entry point substitutes recovered output.
- Nothing reconstructs structure from a file that is not valid Python. Editor recovery
  (`haute._pipeline_recovery.load_pipeline_editor_document`) recovers a valid file statement by
  statement; a syntax-invalid one is a source-only document carrying the located syntax error,
  with no canvas.
- Missing, unreadable, invalid, or schema-invalid config sidecars raise `ConfigError` through the
  strict parser. The editor recovery service resolves recovered node fragments independently and
  represents an attributable failure as an unavailable recovery node plus a diagnostic; a
  canonical graph never carries a failed node.
- A referenced submodel file is parsed atomically. If its module AST is invalid, parsing raises
  `ParseError` naming the child file and syntax location rather than merging an empty warning
  graph that has lost every child node and edge.
- A registration names its definition through the file's submodel imports: its first argument
  is `<name>.submodel` for a module the file imports as `from modules import <name>`, which
  names `modules/<name>.py` beside the pipeline. That file is resolved relative to the pipeline
  directory within the project root, and every registered definition must resolve to a
  readable file. Missing files raise one `ParseError` that lists every unresolved path instead
  of returning a graph with those submodels omitted.
- The imports and registrations have one canonical form, and the parser refuses every other
  with a `ParseError`: an import from `modules` in another spelling (relative, dotted,
  `import modules...`, an alias, `*`), a module imported twice, a module whose name is not a
  canonical identifier as a node's is (ASCII letters, digits and underscores), starts with `__`
  or would hide a Python built-in, a registration whose first argument is not `<name>.submodel`
  for an imported `<name>`, and, reading a whole file, an import no registration uses. A
  registration by file path (positional or `file=`) is the removed form: its `ParseError`
  carries the exact import form as its remediation, and nothing migrates it.
- An in-memory `parse_pipeline_source` call that contains submodel registrations must supply
  `_base_dir` or `_submodel_base_dir`. Without a resolution root the parser raises `ParseError`
  with every unresolved path; returning only the root nodes would violate the same
  conservation contract as a missing file.
- Several registrations may name one definition module. The file is parsed once and
  each registration's name becomes a separate occurrence.
- One definition id may resolve to only one file, and one file may declare
  only its matching definition id. Conflicts raise `ParseError` before a
  registry entry is constructed.
- `flatten=True` expands every occurrence into independently qualified runtime
  nodes for executor, trace, and deploy. The default keeps canonical
  occurrence nodes plus a definition registry for the GUI.
- Parent connections address occurrence aliases and must name declared public
  input/output port ids. Internal child ids never become parent endpoints or
  public handles.
- Every submodel source must declare literal `definition_id`,
  `input_ports`, and `output_ports` on `haute.Submodel(...)`. The deprecated
  `outputs=` shape is rejected rather than inferred or migrated.
- Nested submodels (a submodel file itself calling `pipeline.submodel(...)`, whatever its
  arguments) are capped at one level. Parsing raises `ParseError` naming the containing file and
  the line of every nested registration (`nested_lines`), because returning the outer graph
  while omitting the nested references would not conserve authored structure.
- Editor recovery discovers every construct of a valid file it can locate and unambiguously
  describe, and conserves a malformed or ambiguous authored construct as a recovery element or
  attributed diagnostic rather than silently dropping or guessing it.
- Preamble boundaries recognise both module aliases (`import haute as ht`) and direct constructor
  aliases (`from haute import Pipeline as BuildPipeline`), including multiline pipeline
  construction.
- A pipeline file's imports from `modules` are generated code, never preamble: codegen writes
  them from the registrations, so the preamble leaves out their lines wherever they sit. In a
  submodel definition file such an import is ordinary preamble.
- Before the strict parser returns, a structure-conservation gate checks the authored node ids,
  explicit edge endpoints and handles, implicit parameter edges, and submodel references against
  the constructed graph. A parent edge endpoint names a root node or a registered occurrence:
  a submodel's own nodes are reached only through its occurrence's ports, so an endpoint naming
  one is dangling like any other unknown name, refused with an actionable `ParseError`. Exact duplicate
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
- **A name reads the value it held where the formula ran.** Node code runs as one module-level
  namespace, so `factor = 2`, a `with_columns` call reading `factor`, then `factor = 3` computed
  with 2, and the formula shows 2. A binding keeps the names it read when its own statement ran.
  A name assigned in a way the code does not show statically before the formula's statement —
  inside control flow, by an augmented or unpacking assignment — has no value the parser can
  prove: the formula is opaque, and evaluating it reports `unresolved_name: <name>` rather than
  computing with a stale binding or the preamble's value of the same name.
- **Every fact comes from the located expression.** `evaluate_expression` types an expression
  `window` when the expression that assigns the column contains an `.over()` call, describes each
  window in words from its syntax tree (`rank of x over g`, or `10.0 / (sum of a over g)` for one
  inside arithmetic), and adds the partition columns it names as strings to its referenced
  columns. Another column of the same node, even one in the same call, keeps its own type,
  substituted text and columns.
- **Substitution renders, never rewrites text.** The substituted text is the located expression
  written again from its syntax tree with each column reference replaced by the row's value and
  each literal preamble or global constant read by its value; quoted literals, method names and
  keywords stay as written, so a literal spelled like a column (`concat_str(x, "x")`) or a
  column named like a method keeps its meaning. Operators keep the grouping the tree gives them:
  a unary operator keeps the parentheses of a looser operand, so `-(x + z)` never reads as
  `-x + z`.
- `evaluate_expression(code, target_column, row_values, ..., row=None)` additionally
  substitutes concrete column values into the formula text and has **Polars** compute the result
  on the traced row. `row` is that row as a one-row frame in the dtypes the pipeline gave it; the
  trace always supplies it. Without it the row is built from `row_values`, which must then be
  native Python values. The same-node names the expression uses are resolved, literal preamble
  constants are inlined, and the expression is built through the sandbox's validator and
  restricted globals with the preamble's names. Polars' own semantics apply throughout (null
  propagation, Kleene logic, division by zero, integer wraparound, rounding), because Polars
  computes the value.
- **One row determines the value only when the expression is row-local.** An expression is
  computed on the row only when the row-locality classifier, in its row-semantics mode, proves
  that every operation in it is registered row-local. A window, aggregation, shift, rank, diff,
  cumulative or unregistered operation would show a value the pipeline never produced if
  evaluated on one row. So such a result stays `None`, and `not_computable_reason` names why:
  `not_row_local: <operator>`, `unresolved_name: <name>`, `column_unavailable: <column>`,
  `traced_row_unavailable` (the trace could not recover the typed row), or
  `expression_not_located`. Where one row cannot compute the formula that assigns a step's
  column, the trace shows that column's value from its own execution — the full-context value —
  marked `result_source: "trace_execution"`; see [tracing](../tracing/high-level.md).
- For `pl.when()/.then()/.otherwise()` conditionals, evaluation additionally reports which branch
  was actually taken (and, for nested conditionals, which branch was taken at each inner level),
  so the trace UI can highlight the live branch and dim the others. Each condition is evaluated
  by Polars on the row in source order; when a condition is not computable from one row, the
  branch is left unreported rather than guessed.
- `parse_expression_chain(code, target_column)` walks backward through the same node's
  `with_columns()` calls to build the transitive dependency chain feeding `target_column` — every
  intermediate column referenced along the way, in dependency order (earliest first). A syntax
  error is converted into a one-element opaque chain (or `[]`), while non-syntax internal failures
  are not caught.

**Global constants.** The strict parser reads the pipeline constructor's `global_constants`
keyword, which may name only `config/global_constants.json`, and loads that file into the
graph. A missing, unreadable or invalid file, or one reached through a link out of the
pipeline folder, never fails the parse: the graph carries no
constants, the reason as `global_constants_error`, and a warning. The generated
`global_constants = pipeline.global_constants` (or `submodel.global_constants`) is recognised as
generated code; any other module-level binding of the name, a node function of that name, and a
submodel constructor that declares constants are `ParseError`s naming the line.

## Design rationale

- The structural parser uses Python's `ast` module because pipeline files are executable Python
  and this path only needs structural reads. Codegen is a separate generation path: its builders
  describe each node and one printer lays the module out, a contract with the rest of its
  decorator, so no generated source is edited afterwards. A rewrite of authored source (Recover,
  Reset, the node-scoped save) goes through the LibCST-based structured-syntax boundary in
  `haute._python_syntax`; nothing unparses this parser's AST over authored source.
- Editor recovery exists so that one node's configuration or contract failure need not blank the
  editor while strict runtime consumers still fail loudly. It reads the node and connection
  skeletons of a file that parses and resolves each node, under a named isolation boundary, into
  its structurally incompatible recovery DTOs. A file that does not parse has no structure to
  read, so it loads as source only rather than as a guessed canvas.
- Polars computes every traced value. A hand-written interpreter once re-implemented Polars'
  semantics to avoid executing user code for a display question. Project code is trusted, and
  the same expression has already run in the same execution, so reimplementing Polars only
  added drift. Polars on the traced row's own typed slice gives the value the pipeline would
  give that row.
- Row-locality is decided statically, from the operation registry's row semantics, before
  anything is evaluated. Evaluating a multi-row form on one row succeeds and returns a
  plausible, wrong number, so it must never be attempted.
- A Polars failure on a computable expression propagates to the enrichment caller, which
  records a visible error marker. A result is never replaced by `row_values.get(target_column)`,
  because a laundered value looks self-consistent with the trace and hides a real failure. This
  is a direct instance of this codebase's "fail loud, never guess" principle (see the project's
  `CLAUDE.md`).
- The module-level `_cached_parse` (an `lru_cache` over `ast.parse`) exists because
  `parse_expression`, `_locate_defining_expression`, and `parse_expression_chain` each reparse
  the *same* code string for the same node; caching is safe
  because every consumer only reads the cached tree — substitution/reassignment-chain resolution
  build new AST nodes rather than mutating it in place.

## Interactions

- Depends on [pipeline-config](../pipeline-config/high-level.md) for project-root inference and
  config file loading (`_project.get_project_root`, `_config_io.load_node_config`,
  `_config_io.has_config_folder`).
- Depends on [codegen](../codegen/high-level.md) for the AST/source utility and user-code
  extraction primitives shared by generation and parsing.
- Depends on [pipeline-config](../pipeline-config/high-level.md) for strict config-dict
  construction and canonical node/edge builders. Editor recovery resolves each node through the
  same config-dict construction but builds its own recovery models, not those canonical objects.
- Depends on and cooperates with [submodels](../submodels/high-level.md): this component decides
  when a registered definition must be resolved and parsed, using that component's
  `modules/<name>.py` naming of a definition module, but the occurrence-node
  construction, boundary-port classification, and flatten algorithm live there.
- `PipelineGraph`, the output type, is the canonical structure shared with the executor, codegen,
  and the server API layer — changes here are visible everywhere a pipeline is rendered or run.
- The expression-parsing half is consumed by the trace/execution-engine enrichment layer, which
  attaches `ParsedExpression`/`EvaluatedExpression` to each executed step for the trace UI.
- Uses [sandbox-security](../sandbox-security/high-level.md)'s accident guard and execution
  globals to build the one expression `evaluate_expression` evaluates, and the
  [execution engine](../execution-engine/low-level.md)'s row-locality classifier to decide
  whether one row determines it.

## Failure model

- Structural parsing converts a whole-file `SyntaxError` into contextual `ParseError`; no
  syntax-invalid source returns a canonical `PipelineGraph`.
- Structural parsing also raises `ParseError` for authored content it cannot
  safely reconstruct: an unclosed `connect()`/`submodel()`/decorator argument list, a non-literal
  decorator keyword argument, a submodel import or registration not in the canonical form (the
  removed file-path registration among them, refused with the import form that replaces it)
  or an import no registration uses, an
  `async def` pipeline node, a missing submodel
  file, a submodel registration without a resolution root, conflicting
  definition-to-file registrations, duplicate instance ids or aliases,
  missing canonical identity fields, invalid structured public ports, or a
  nested submodel
  reference, an exact duplicate edge identity, or any node/edge/handle identity rejected by the
  conservation gate.
- Name checks are of two kinds. A *structural* collision of canonical graph identities within
  one graph stays a `ParseError`, because the colliding entries would collapse into one node
  id: two functions of one name in a file, two submodel occurrences of one name, an
  occurrence named like a root node. Such a file goes to editor recovery. Every other
  violation of the codegen specification's executable-name rule (`haute._executable_names`:
  names equal ignoring case across the root and submodel graphs, a reserved or built-in node
  name, a reserved node input, and, when the parse has a base directory to read utility files
  from, the support-code collisions of the codegen specification) is *semantic*: it is
  collected after the graph is built,
  without discarding the graph. `parse_pipeline_source_with_name_violations` returns the
  graph with its violations; `parse_pipeline_source` and `parse_pipeline_file` (the strict
  form `haute run`, deploy and codegen's post-save parse use) raise one `ParseError` listing
  every violation, so those refuse such a file before running anything. The editor document
  loader takes the graph and its violations (server-api). A parse that silently returned a plausible-but-incomplete graph here would
  corrupt the file on the next save, which this codebase treats as strictly worse than a loud
  failure.
- Config sidecar load/validation failures are raised as `ConfigError`. Editor-only recovery
  diagnostics are not embedded into canonical node configuration.
- `parse_expression` never raises: any internal exception becomes an `"opaque"` result. This is
  the single deliberate catch-all in the expression parser, justified as the honest "could not
  statically understand this" signal.
- A value one row cannot determine is `None` with a `not_computable_reason`; the evaluator
  never substitutes the receiver or `row_values[target_column]` as a plausible result. A Polars
  failure on a computable expression propagates to the trace/enrichment caller, which records a
  visible error marker. `parse_expression_chain` does
  not catch non-syntax internal failures. It does catch `SyntaxError` and
  returns the opaque `parse_expression` result as a singleton when available.

## Strict pipeline parsing and editor-only recovery

`parse_pipeline_file()` and `parse_pipeline_source()` are strict canonical entry points. A
whole-file `SyntaxError`, invalid decorator, configuration error, topology error, or submodel
error raises a contextual Haute exception; neither entry point substitutes a recovered graph.
Execution, lint, deploy, code generation, and post-save verification therefore never consume
recovered output.

The skeletons recovery reads are neutral: pipeline metadata, authored decorator and function
identity, parameters, body text, declared connections, submodel registrations, and source spans.
The editor recovery service is the sole consumer that may resolve them into the separate
recovery DTOs defined by the server API. Those DTOs never inherit from, return, or
deserialize as `PipelineGraph`. Expected node-local authored errors become attributed diagnostics
at that editor boundary; strict parsing continues to propagate the original error unchanged.
A strict refusal that no element's diagnostic shows is listed as a pipeline-level
`pipeline_parse_invalid` diagnostic, whatever else is on screen, so an unrelated problem never
hides it. One counts as shown when a diagnostic carries its message, or when the recovery pass
reports that refusal element by element in its own words (an unknown decorator, a duplicate
node name, a dangling or duplicate connection, a missing submodel file:
`_STRICT_FAILURE_SHOWN_AS`). A pipeline name or description that is not a literal is the
constructor's failure, not a node's: the document loads around it with a
`pipeline_constructor_invalid` diagnostic and the file's name as the pipeline's.
