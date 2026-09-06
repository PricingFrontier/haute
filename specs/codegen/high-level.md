# Codegen — High-Level Specification

## Purpose

Haute pipelines are authored visually as a React Flow graph (nodes + edges) but
executed as plain Python. Codegen is the one-way bridge from the visual
representation to human-readable Python: a standalone, re-runnable `.py` file
for a flat graph, or a small parseable file tree for a graph with submodels. The
valid-Python mutation and classification boundary is fixed by the accepted
[structured syntax decision](structured-syntax-boundary.md). Given a validated
`PipelineGraph`, it produces source that:

- Imports `polars` and `haute`, constructs a `haute.Pipeline`/`haute.Submodel`
  object, and defines one decorated function per node (`@pipeline.<type>` /
  `@submodel.<type>`).
- Wires nodes together with `pipeline.connect(...)` calls that mirror the
  graph's edges, including multi-frame port names.
- Embeds enough metadata (docstrings, decorator kwargs, JSON config sidecars,
  column contracts, preserved free-form blocks) that the file can later be
  parsed back into an equivalent graph — see
  [pipeline-config](../pipeline-config/high-level.md) for the sidecar format
  and the parsing counterpart in `src/haute/parser.py`.

The generated file tree is the artifact saved to disk and later parsed for preview,
execution, tracing, and deployment; it is not an intermediate serialization hidden from the
user. A single-file/flat generated pipeline is also directly executable through
`haute.Pipeline.run()`. A hierarchical main file is different: its live
`pipeline.submodel(path)` calls only record paths, so the live `Pipeline` API does not import the
child registrations and the main file is not a standalone execution surface for that hierarchy.
The static parser resolves and flattens the child files before the full executor runs them.

## Scope

In scope:

- Assembling a full pipeline/submodel file from a topologically sorted node
  list and edge list (`haute.codegen.graph_to_code` /
  `haute.codegen.graph_to_code_multi`).
- Per-node-type source generation (the `_gen_*` builders in
  `haute._codegen_builders`), covering every `NodeType` registered in
  `haute._registry.NODE_REGISTRY`.
- Injecting column-contract decorator kwargs (`_format_contract_kwarg` /
  `_inject_contract_kwarg` in `src/haute/codegen.py`) through the
  formatting-preserving LibCST boundary in `src/haute/_python_syntax.py`.
- Extracting the user-authored portion of a node's code editor content back
  out of generated boilerplate (`haute._code_extraction`), so the same text
  can be re-embedded on the next save without accreting duplicate scaffolding.
- Low-level AST/source utilities shared with the parser
  (`haute._ast_helpers`): docstring stripping, dedent, decorator inspection,
  preamble/preserved-block extraction.
- The final "does it even parse" gate (`_assert_emitted_files_parse`).

Out of scope (owned by neighbouring components):

- Orchestrating emitted `.py` files back into a `PipelineGraph` —
  `src/haute/parser.py`, `src/haute/_parser_regex.py`, and
  `src/haute/_parser_submodels.py` are owned by
  [expression-parsing](../expression-parsing/high-level.md). Parsed node/config conversion in
  `src/haute/_graph_builders.py` is owned by
  [pipeline-config](../pipeline-config/high-level.md). Codegen shares
  `src/haute/_ast_helpers.py` and `src/haute/_code_extraction.py` with those read paths because
  generation and extraction are two directions of the same contract (see Interactions).
- Declarative per-node JSON sidecar read/write and folder conventions —
  [pipeline-config](../pipeline-config/high-level.md) (`haute._config_io`).
- Evaluating user-authored Polars expressions / rating formulas at runtime —
  [expression-parsing](../expression-parsing/high-level.md).
- Packaging and shipping the generated file tree to a deployment target —
  [deploy](../deploy/high-level.md) (`src/haute/deploy/`).
- Executing the generated functions at graph-preview time — the executor
  (`haute._builders`) has its own per-type builders that produce runtime
  closures rather than source text; codegen's builders are its source-level
  mirror, paired 1:1 through `NODE_REGISTRY`.
- Validating the graph's structural shape (dangling edges, missing nodes,
  role assignment) — `haute._graph_shape`, `haute._topo`, and
  `haute._edge_join`, which codegen calls into but does not implement.

## Behaviour

- **Deterministic given the same graph.** Node order follows a topological
  sort (`haute._topo.topo_sort_ids`); contract dicts and column sets are sorted, while connect
  calls preserve the graph's edge order (apart from edge-join role ordering and root-boundary
  deduplication). The same ordered graph therefore produces byte-identical output; codegen does
  not canonicalise arbitrary input edge ordering.
- **Dangling graph edges fail before source emission.** Codegen uses the strict
  `topo_sort_ids` boundary and propagates `UnknownEdgeEndpointError` with the
  deterministic unknown-node and dropped-edge evidence. It never silently removes a
  malformed connection from the generated pipeline.
- **One function per node**, named by sanitizing the node's label
  (`haute._graph_utils._sanitize_func_name`). Any two node labels or
  submodel occurrence aliases that produce the same identifier, including exact duplicate labels, are a hard error at codegen time
  (`_error_on_name_collisions`), checked globally across the root graph and
  every submodel — not per file — because the flattened runtime graph is
  keyed by the sanitized name across module boundaries.
- **Function parameters are the listed input names, 1:1.** Each parameter of a
  generated node function is the *input name* of one incoming edge, derived by
  `haute._graph_utils.edge_input_name` in edge order: an `apiInput`-frame edge
  contributes its frame label verbatim (labels are validated as ASCII Python
  identifiers by the api-input schema), a submodel-output edge contributes the
  occurrence's own name (or f"{alias}__{port_id}" when declaring more than one output port), and every ordinary
  edge contributes the sanitised source-node label. A frame emitted as `quotes` is therefore callable as
  `quotes` in every downstream body — the same string the editor lists as the
  input. When a canvas topology rewrite replaces a Polars node's parent while
  preserving the authored input's meaning, `inputMapping` records
  `{logical_name: current_edge_name}`. Codegen then keeps the logical name in
  the function signature and emits the mapping on `@pipeline.polars(...)`,
  while the `connect(...)` call continues to name the current parent. The
  parser must retain that mapping so repeated save/reload cycles are stable.
  Two incoming edges of one node deriving the same effective parameter name are a
  hard `ParseError` at codegen time; parameters are never disambiguated with
  hidden numeric suffixes. Every `apiInput` edge emits an explicit
  `source_port` in its connect call — including a sole-frame source — so the
  file itself always names the frame each parameter binds.
- **Submodel-aware.** A graph with no submodel occurrences produces exactly
  one file. A hierarchical graph emits each referenced definition file once,
  in first-occurrence order, plus a main file with one explicit
  `pipeline.submodel(path, definition_id=..., instance_id=..., alias=...)`
  registration per occurrence. Distinct definitions may not share
  a file, unused registry definitions are rejected, and occurrence ids and
  aliases are never inferred. Parent connections name declared public port
  names; `in__<name>`/`out__<name>` exist only in graph JSON and are not
  emitted as authored parameter names. Inside a definition, child
  parameters use sanitised public input port names. Downstream, an occurrence
  contributes its own name (or <alias>__<port_name> when declaring more than one
  output port) as the executable input name; public ports declare a single
  canonical name: portId and label are not emitted.
  `graph_to_code` refuses a hierarchical graph rather than returning an
  arbitrary file. Each definition file carries its declared
  `definition_id`, complete literal `input_ports`/`output_ports` contract,
  description, preamble, and preserved blocks. Unused declared outputs
  therefore survive parse/save/reload without inference from parent edges.
- **Config-folder rewrite.** Node types with a declarative JSON sidecar
  (`haute._config_io.has_config_folder`) get their decorator's inline kwargs
  replaced with a single `config="config/<type>/<name>.json"` reference after
  the type-specific body is generated. The config content itself is written
  separately by the config-io save path. A config-backed type without a
  registered decorator, or a builder result without a function definition,
  is a `HauteError`; codegen never defaults to a generic decorator or silently
  skips the rewrite.
  The generated `_HAUTE_CONFIG_BASE` import and assignment are module
  infrastructure, emitted exactly once outside authored preamble ownership.
  Its value always resolves to the parent pipeline directory: a pipeline file
  uses its own directory, and a submodel file climbs one level per path
  segment in its recorded registration path, so definitions registered at any
  depth inside the project resolve config paths identically at parse time and
  at generated-module runtime.
  Reverse parsing likewise removes the generated per-node loader scaffold;
  neither its imports nor its load call may enter editable node `code` or be
  executed by the user-code sandbox.
- **Canonical data I/O generation.** `dataInput` emits
  `@pipeline.data_input(config="config/data_input/<name>.json")`, loads the
  configured input through the shared helper (a derived direct Parquet scan
  or a published snapshot),
  binds the result to `df`, runs optional user Polars code, then returns
  `df`. `dataOutput` emits
  `@pipeline.data_output(config="config/data_output/<name>.json")` with a
  side-effect-free pass-through body: persistence happens only through the
  explicit output-write execution surface, never on import or an ordinary
  `Pipeline.run()`. Removed `dataSource`/`dataSink` forms are neither emitted
  nor accepted as codegen node types.
- **Retained sidecar inputs stay live.** API Input and External File bodies
  contain only their sidecar path and call the shared config-driven loaders
  with `Path(__file__).resolve().parent` as the pipeline-directory candidate.
  They do not bake the sidecar's current data path, schema, file type, or
  model class into source. A sidecar-only edit therefore changes the next
  generated-function execution, and malformed sidecars raise the same
  validation error as canvas execution.
- **Contract kwarg injection.** Every ordinary node gets a `contract=...` decorator kwarg
  documenting its column-level input/output contract (or the string sentinel `"opaque"` when it
  cannot be determined statically). An instance node with no explicit declared contract omits the
  kwarg because it inherits the original node's contract; an instance carrying a declaration has
  that declaration emitted. Injection rewrites already-generated source text in place (see Design
  rationale), rather than templating the kwarg in from the start.
- **Instance mappings are persisted, not merely baked into one function body.** An
  instance emits both `of=...` and its explicit `inputMapping=...` on the
  decorator, as well as the resolved keyword call to the original function.
  Parsing therefore restores the mapping metadata needed for a later graph
  edit or save instead of relying on name heuristics after the first reload.
- **Polars transforms bind inputs by name only.** A `polars` node's function
  parameters are its logical inputs, one per incoming edge in edge order; `df` is
  purely the output variable and is never pre-bound to an input. Generated
  bodies declare `df` as an unbound local, then emit the user's code and the
  appended `return df`; a same-named preamble global therefore cannot satisfy
  a missing output assignment. Normally the logical name is the edge-derived
  name; a non-instance Polars `inputMapping` may preserve an earlier logical
  name across a structural rewrite. Mapping values must match distinct current
  edge names and the resulting logical names must remain distinct valid Haute
  identifiers, otherwise generation and execution fail loudly. The user starts
  from the input they mean by name and assigns the result to `df`. `explore`,
  `external_file`, and the post-code hooks (`data_input`, `rating_step`,
  `scenario_expander`, `model_score`) keep their single implicit `df` frame;
  only `polars` transforms carry the named-input contract.
- **User code round-trips.** Text typed into a node's code editor is
  embedded into the generated function body, wrapped with generated
  boilerplate (imports, config-driven loads, a trailing `return df`). On the
  next save, `haute._code_extraction` strips exactly that boilerplate back
  out before re-wrapping, so repeated edit/save cycles do not accumulate
  duplicate scaffolding or lose the user's formatting/comments.
- **Preserved blocks.** Free-form module text wrapped by column-zero
  `# haute:preserve-start` / `# haute:preserve-end` markers in a pipeline or
  submodel file survives regeneration and is re-emitted after object
  construction and before generated node functions. These completed spans
  are excluded from preamble extraction, so repeated parse → generate cycles
  reach a source fixpoint instead of duplicating the block. Indented markers
  are owned by their enclosing function or construct and remain there; they
  are not separately extracted or relocated to module scope. Leading/trailing
  blank lines inside a completed module block are stripped, and unmatched
  module-level starts are ignored.
- **Fails loudly, never emits a corrupt file.** Every code path that could
  produce invalid Python — a missing codegen builder, an invalid structured
  source edit, an unparseable emitted file — raises rather than degrading to a
  partial or passthrough result. See Failure model.

## Design rationale

- **Text generation with a structured mutation boundary.** Bodies are built from format
  strings and f-strings, not `ast.unparse` or a templating engine, so the
  emitted files read like hand-written Python and are directly diffable by a
  human reviewer. String-safety for initial interpolation remains explicit in
  `_safe_str`, `_safe_path`, and `_sanitize_description`. Post-generation
  source mutation is different: it must pass through the LibCST boundary so
  comments and untouched formatting have one owner and callers never splice a
  manually located delimiter.
- **Contract injection is a post-hoc source rewrite, not part of the
  template.** Each `_gen_*` builder produces its decorator without knowing
  about contracts; `_inject_contract_kwarg` parses the generated module and
  keyword through `haute._python_syntax.inject_decorator_keyword`, changes the
  first structured `@pipeline.*` or `@submodel.*` decorator call, and emits the
  updated CST. A column literally named `"price (gbp)"`, a comment containing a
  decorator, or multiline trivia therefore cannot become an insertion point.
  This keeps contract computation (which can hit `ConfigError` or need an
  MLflow round-trip) decoupled from the per-type body templates.
- **One proven shared node declaration, not inferred parity.** The modelling
  node's first-connected-input passthrough policy and decorator config keys are
  declared once in `haute._registry` and consumed by both its runtime and
  codegen builders. Other node types retain explicit builders until a direct
  cross-path result test proves that their semantics genuinely match.
- **Global collision scope, not per-file.** A root-graph node and a
  submodel-child node emit into different `.py` files (legal at the file
  level), but `flatten_graph` later merges every submodel into one
  execution graph keyed by sanitized function name. Catching collisions
  per-file would let a genuinely fatal cross-module shadowing bug through
  to runtime; `_error_on_name_collisions` is deliberately global.
  Consequently, renaming a node in one submodel can be rejected because of a
  same-named node in an unrelated submodel. That wider authoring error surface
  is the accepted cost of preventing silent execution-time shadowing.
- **`OSError`/`mlflow.*` are the only contract-computation errors treated as
  "opaque," not fallback-worthy.** `_is_codegen_infra_error` narrowly
  allowlists environmental failures (missing artifact, unreachable MLflow
  server) so codegen can save a pipeline in a disconnected/CI environment
  without a running model server. Every other exception — misconfiguration,
  a genuine bug in contract computation — propagates and fails the save;
  masking those behind `contract="opaque"` would hide a real defect inside a
  file that then runs and fails far from the cause.
- **Optimiser / modelling / explore bodies are genuine first-frame
  passthroughs.** Their actual computation (solving, training) happens via
  dedicated API routes, not by running the generated function — so a
  passthrough body is runtime-equivalent, not a shortcut that silently
  drops behaviour.
- **Boilerplate stripping is AST-based, not line-based.** Determining which
  `return` belongs to the outer node-body scope (vs. a nested `def`/`class`/
  `lambda` the user wrote) cannot be done reliably by string matching —
  comments, string literals containing the word "return," and multi-line
  `return (...)` all defeat a textual scan. `_code_extraction._outermost_returns`
  walks the AST and stops descending at any node that opens a new scope.
- **Parameter names are semantic, never cosmetically deduplicated.** An
  earlier design derived every parameter from the source-*node* label and
  suffixed duplicates (`name_2`) because binding is positional and the names
  were "cosmetic". That made the two frames of one `apiInput`
  indistinguishable in code (`Quote_Input_1` vs `Quote_Input_1_2`), hid the
  real frame names the editor displays, and — worse — meant reconnecting
  edges in a different order silently re-bound an unchanged body to different
  frames. Deriving each name from its own edge (`edge_input_name`) makes the
  name travel with the frame: binding is still positional in mechanism, but a
  reorder reorders the signature rather than re-meaning a name, and a
  collision is a loud error instead of a hidden rename.

## Interactions

- **Depends on** [pipeline-config](../pipeline-config/high-level.md)
  (`haute._config_io`) for config-folder path conventions and to know which
  node types get their kwargs rewritten to a `config=` reference.
- **Depends on** `haute._registry.NODE_REGISTRY` as the single source of
  truth for which builder handles which `NodeType`; a missing codegen entry
  is a registry wiring bug, not something codegen falls back for.
- **Depends on** `haute._graph_shape`, `haute._edge_join`, and
  `haute._topo` for graph-shape validation, edge-join role resolution, and
  topological ordering before any source is emitted.
- **Shares** `src/haute/_ast_helpers.py` and `src/haute/_code_extraction.py` with the parser
  (`src/haute/parser.py`, `src/haute/_graph_builders.py`,
  `src/haute/_parser_regex.py`,
  `src/haute/_parser_submodels.py`) — generation and extraction
  are two halves of one round-trip contract; a change to how codegen wraps
  user code generally requires a matching change to how extraction unwraps
  it.
- **Supplies canonical user-code text to** `haute.chunking`: chunk planning
  reads the parsed `dataInput` code field and applies its own row-locality
  proof to the same boilerplate-free text that codegen re-emits.
- **Depended on by** the save-pipeline route, which calls
  `graph_to_code_multi` to produce a multi-file tree and `graph_to_code`
  for graphs that produce one pipeline file.
- Parsed configs contain user code only. Runtime builders, projection, deploy,
  and code generation consume that canonical text directly; they do not accept
  generated function bodies as another config-code representation.
- Contract computation calls into `src/haute/_contracts.py`
  (`get_column_contract`, `Contract`), whose callbacks are registered by the execution builders;
  this is shared registry/contract infrastructure, not expression evaluation.

## Failure model

Haute prefers loud failure to silent fallback; codegen is one of the places
that discipline matters most, because a silently-corrupt `.py` file would
only surface as a confusing failure much later (at parse time, or worse, at
execution time on a mis-wired pipeline). Concretely:

- **No codegen builder registered for a `NodeType`** → `KeyError`, raised
  from `haute.codegen._generate_node_code`. This is a registry wiring
  defect (every `NodeType` must have both an exec and a codegen builder per
  `NODE_REGISTRY` contract), never silently handled by falling back to a
  generic transform template.
- **Config-folder rewrite has no decorator mapping or no generated `def`** →
  `HauteError` with the node id, label, and type. Codegen never substitutes
  `@pipeline.polars` or leaves stale inline decorator arguments behind.
- **Generated source or the injected keyword is invalid structured Python, the
  keyword already exists, or no `@pipeline.*`/`@submodel.*` decorator was found** →
  `HauteError` from `_python_syntax` / `_inject_contract_kwarg`, carrying a
  stable reason and source position when parsing reached one,
  enriched with the offending node's id/label/type before re-raising.
- **Contract computation raises `ConfigError`** (user misconfiguration) or
  any other non-infra exception → propagates unchanged; only `OSError` and
  `mlflow.*` exceptions are downgraded to an opaque contract.
- **`inputs_by_parent` has two distinct source keys colliding on the same
  emitted parent with different columns** → `ParseError` from
  `_format_contract_source`; ambiguous data is never silently resolved by
  "keep the last writer."
- **Node label collisions** (two node labels or submodel occurrence aliases sanitizing to the same Python
  identifier, including exact duplicates, anywhere in the root graph or any submodel) →
  `ParseError` enumerating every colliding bucket, from
  `_error_on_name_collisions`.
- **An edge references a node absent from the graph** →
  `UnknownEdgeEndpointError` from the shared strict topology boundary before any
  generated source is accepted; codegen does not use filtered traversal.
- **Input-name collisions on one node** (two incoming edges deriving the same
  parameter name — e.g. a frame labelled `clean_data` alongside an upstream
  node whose label sanitises to `clean_data`) → `ParseError` naming the
  target node and the colliding input name; never a silent `_2` suffix. The
  frontend rejects creating such a connection at drag time with the same
  rule, so this backend error is the authoritative backstop, not the first
  line of defence.
- **Unpersistable or malformed submodel boundary edge** (a missing or
  wrong-prefixed `in__`/`out__` handle, an undeclared public port id, an
  occurrence with malformed identity, or a definition port whose internal
  endpoint is invalid) -> `ParseError` with exact definition, instance, port,
  and edge context before any source is accepted.
- **`graph_to_code` called on a graph that actually has submodels** →
  `ConfigError`, because silently returning "the first file" would hand back
  an arbitrary submodel file instead of the main pipeline.
- **Any emitted file fails `ast.parse`** → `ConfigError` from
  `_assert_emitted_files_parse`, the final gate before a save is allowed to
  land on disk. Includes the offending file, line, and message text so a bad
  node-code block or a codegen bug is directly actionable; the save route
  wraps this in a transaction and attempts to restore every touched file.
  Rollback is best-effort: a compensating filesystem operation can itself
  fail, in which case that rollback failure is logged while the original
  save error remains the client-visible failure.
- **Unparseable user-authored code passed into extraction** →
  `_UserCodeParseError` (a `ParseError`/`ValueError` subclass) from
  `haute._code_extraction._parse_user_code`, naming which extractor was
  running and the original `SyntaxError`'s location.
- **Submodel occurrence node reaches a codegen builder directly** →
  `RuntimeError` from `_gen_submodel_placeholder_unreachable`; this
  indicates `graph_to_code_multi`'s root/child-node filtering has a bug,
  since the occurrence should never be dispatched on.
