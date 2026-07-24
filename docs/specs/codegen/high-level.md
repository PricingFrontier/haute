# Codegen — High-Level Specification

## Purpose

Haute pipelines are authored visually as a React Flow graph (nodes + edges) but
executed as plain Python. Codegen is the one-way bridge from the visual
representation to human-readable Python: a standalone, re-runnable `.py` file
for a flat graph, or a small parseable file tree for a graph with submodels. Given a validated
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
  `_inject_contract_kwarg` in `src/haute/codegen.py`) via a token-aware source
  rewrite.
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
  [deploy](../deploy/high-level.md) (`src/haute/deploy/`), which itself imports
  `_strip_generated_boilerplate_from_code` for its own scoring code path.
- Executing the generated functions at graph-preview time — the executor
  (`haute._builders`) has its own per-type builders that produce runtime
  closures rather than source text; codegen's builders are its source-level
  mirror, paired 1:1 through `NODE_REGISTRY`.
- Validating the graph's structural shape (dangling edges, missing nodes,
  role assignment) — `haute._graph_shape` and `haute._edge_join`, which
  codegen calls into but does not implement.

## Behaviour

- **Deterministic given the same graph.** Node order follows a topological
  sort (`haute._topo.topo_sort_ids`); contract dicts and column sets are sorted, while connect
  calls preserve the graph's edge order (apart from edge-join role ordering and root-boundary
  deduplication). The same ordered graph therefore produces byte-identical output; codegen does
  not canonicalise arbitrary input edge ordering.
- **One function per node**, named by sanitizing the node's label
  (`haute._graph_utils._sanitize_func_name`). Two distinct node labels that
  sanitize to the same identifier are a hard error at codegen time
  (`_error_on_name_collisions`), checked globally across the root graph and
  every submodel — not per file — because the flattened runtime graph is
  keyed by the sanitized name across module boundaries.
- **Function parameters are the listed input names, 1:1.** Each parameter of a
  generated node function is the *input name* of one incoming edge, derived by
  `haute._graph_utils.edge_input_name` in edge order: an `apiInput`-frame edge
  contributes its frame label verbatim (labels are validated as ASCII Python
  identifiers by the api-input schema), every other edge the sanitised
  source-node label. A frame emitted as `quotes` is therefore callable as
  `quotes` in every downstream body — the same string the editor lists as the
  input. Two incoming edges of one node deriving the same parameter name are a
  hard `ParseError` at codegen time; parameters are never disambiguated with
  hidden numeric suffixes. Every `apiInput` edge emits an explicit
  `source_port` in its connect call — including a sole-frame source — so the
  file itself always names the frame each parameter binds.
- **Submodel-aware.** A graph with no `graph.submodels` produces exactly one
  file. A graph with submodels produces one file per submodel (default path
  `modules/<name>.py`) plus a main file that imports them via
  `pipeline.submodel(<path>)` and treats submodel boundaries as opaque nodes
  with `out__<child_id>` / `in__<child_id>` handle conventions.
  `graph_to_code` (single-file convenience wrapper) refuses to run on a
  submodel graph rather than silently returning an arbitrary one of the
  files.
- **Config-folder rewrite.** Node types with a declarative JSON sidecar
  (`haute._config_io.has_config_folder`) get their decorator's inline kwargs
  replaced with a single `config="config/<type>/<name>.json"` reference after
  the type-specific body is generated. The config content itself is written
  separately by the config-io save path.
- **Contract kwarg injection.** Every ordinary node gets a `contract=...` decorator kwarg
  documenting its column-level input/output contract (or the string sentinel `"opaque"` when it
  cannot be determined statically). An instance node with no explicit declared contract omits the
  kwarg because it inherits the original node's contract; an instance carrying a declaration has
  that declaration emitted. Injection rewrites already-generated source text in place (see Design
  rationale), rather than templating the kwarg in from the start.
- **User code round-trips.** Text typed into a node's code editor is
  embedded into the generated function body, wrapped with generated
  boilerplate (imports, config-driven loads, a trailing `return df`). On the
  next save, `haute._code_extraction` strips exactly that boilerplate back
  out before re-wrapping, so repeated edit/save cycles do not accumulate
  duplicate scaffolding or lose the user's formatting/comments.
- **Preserved blocks.** Free-form text wrapped in
  `# haute:preserve-start` / `# haute:preserve-end` markers anywhere in a
  previously-saved file survives regeneration and is re-emitted after the
  `Pipeline(...)` construction and before generated node functions. The inner lines keep their
  order and indentation, but leading/trailing blank lines inside each block are stripped and the
  whole block is relocated; unmatched start markers are ignored.
- **Fails loudly, never emits a corrupt file.** Every code path that could
  produce invalid Python — a missing codegen builder, an untokenizable
  decorator, an unparseable emitted file — raises rather than degrading to a
  partial or passthrough result. See Failure model.

## Design rationale

- **Text generation, not an AST/CST builder.** Bodies are built from format
  strings and f-strings, not `ast.unparse` or a templating engine, so the
  emitted files read like hand-written Python and are directly diffable by a
  human reviewer. The trade-off is that string-safety (quoting, escaping,
  paren-matching) has to be handled explicitly at every interpolation point
  — see `_safe_str`, `_safe_path`, `_sanitize_description`,
  `_matching_close_paren` in `src/haute/_codegen_builders.py` /
  `src/haute/codegen.py`.
- **Contract injection is a post-hoc source rewrite, not part of the
  template.** Each `_gen_*` builder produces its decorator without knowing
  about contracts; `_inject_contract_kwarg` locates the decorator's
  parenthesis span with `tokenize` (so a column literally named `"price
  (gbp)"` can't confuse a naive character scan) and splices the kwarg in.
  This keeps contract computation (which can hit `ConfigError` or need an
  MLflow round-trip) decoupled from the per-type body templates.
- **Global collision scope, not per-file.** A root-graph node and a
  submodel-child node emit into different `.py` files (legal at the file
  level), but `flatten_graph` later merges every submodel into one
  execution graph keyed by sanitized function name. Catching collisions
  per-file would let a genuinely fatal cross-module shadowing bug through
  to runtime; `_error_on_name_collisions` is deliberately global.
  > NOTE: this means renaming a node in one submodel can be rejected because
  > of a same-named node in a completely different, unrelated submodel — a
  > surprising error surface for the author of either submodel, but the
  > alternative (silent shadowing at execution time) is worse.
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
  `src/haute/_parser_helpers.py`, `src/haute/_parser_regex.py`,
  `src/haute/_parser_submodels.py`) — generation and extraction
  are two halves of one round-trip contract; a change to how codegen wraps
  user code generally requires a matching change to how extraction unwraps
  it.
- **Depended on by** the save-pipeline route, which calls
  `graph_to_code_multi` to produce the file tree written to disk (and by
  `graph_to_code` for legacy/simple single-file callers).
- **Depended on by** [deploy](../deploy/high-level.md), whose scorer module
  imports `_strip_generated_boilerplate_from_code` directly to re-derive
  user code from an already-generated model-score body.
- **Depended on by** `haute.projection`, which also reuses
  `_strip_generated_boilerplate_from_code` for a non-save code path.
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
- **Decorator argument list cannot be tokenized, or has no matching close
  paren, or no `@pipeline.*`/`@submodel.*` decorator was found at all** →
  `HauteError` from `_matching_close_paren` / `_inject_contract_kwarg`,
  enriched with the offending node's id/label/type before re-raising.
- **Contract computation raises `ConfigError`** (user misconfiguration) or
  any other non-infra exception → propagates unchanged; only `OSError` and
  `mlflow.*` exceptions are downgraded to an opaque contract.
- **`inputs_by_parent` has two distinct source keys colliding on the same
  emitted parent with different columns** → `ParseError` from
  `_format_contract_source`; ambiguous data is never silently resolved by
  "keep the last writer."
- **Node label collisions** (two distinct labels sanitizing to the same
  Python identifier, anywhere in the root graph or any submodel) →
  `ParseError` enumerating every colliding bucket, from
  `_error_on_name_collisions`.
- **Input-name collisions on one node** (two incoming edges deriving the same
  parameter name — e.g. a frame labelled `clean_data` alongside an upstream
  node whose label sanitises to `clean_data`) → `ParseError` naming the
  target node and the colliding input name; never a silent `_2` suffix. The
  frontend rejects creating such a connection at drag time with the same
  rule, so this backend error is the authoritative backstop, not the first
  line of defence.
- **Malformed submodel cross-boundary edge** (missing/wrong-prefixed handle,
  or a handle referencing a child id that doesn't exist in that submodel) →
  `ParseError` from `graph_to_code_multi` / `_resolve_submodel_endpoint`,
  raised during submodel-file generation so the error names the exact
  submodel and edge rather than surfacing later as a confusing error on an
  unrelated child node.
- **`graph_to_code` called on a graph that actually has submodels** →
  `ConfigError`, because silently returning "the first file" would hand back
  an arbitrary submodel file instead of the main pipeline.
- **Any emitted file fails `ast.parse`** → `ConfigError` from
  `_assert_emitted_files_parse`, the final gate before a save is allowed to
  land on disk. Includes the offending file, line, and message text so a bad
  node-code block or a codegen bug is directly actionable; the save route
  wraps this in a transaction so no partial file tree is written.
- **Unparseable user-authored code passed into extraction** →
  `_UserCodeParseError` (a `ParseError`/`ValueError` subclass) from
  `haute._code_extraction._parse_user_code`, naming which extractor was
  running and the original `SyntaxError`'s location.
- **Submodel placeholder node reaches a codegen builder directly** →
  `RuntimeError` from `_gen_submodel_placeholder_unreachable`; this
  indicates `graph_to_code_multi`'s root/child-node filtering has a bug,
  since the placeholder should never be dispatched on.

## Approved change contract — 0.7.0 data I/O code generation

Remaining code-generation improvement work is tracked in the
[pipeline authoring roadmap](../../roadmap/pipeline-authoring.md).

- Codegen has exactly one tabular-input builder and one persistence-output builder:
  `dataInput` emits `@pipeline.data_input(config="config/data_input/<name>.json")`, opens the
  configured direct/cached provider through the shared generated-code helper, binds the result as
  `df`, executes the optional user Polars body, and returns `df`; `dataOutput` emits
  `@pipeline.data_output(config="config/data_output/<name>.json")` and a side-effect-free
  pass-through body for ordinary module execution.
- No `dataSource`/`dataSink` builder, decorator, template, extractor kind, or sidecar rewrite
  remains. Codegen accepts only the retained enum and never translates a removed graph node.
- Generated `dataInput` execution observes the same base-directory path anchoring, snapshot
  identity, cache-only remote execution, code ordering, and config validation as canvas
  execution. Generated `dataOutput` writes only when its explicit helper/endpoint is invoked;
  importing or running the generated pipeline does not persist data.
- Parse → graph → code → parse round trips preserve `inputType`/`outputType`, format/mode,
  source/destination fields, arguments, cache mode, connection references, and input code without
  inventing inactive fields.

Acceptance executes generated direct and cached inputs with and without code, verifies offline
remote-cache use, exercises each output publication class explicitly, round-trips multiple
inputs/outputs and submodels, and asserts registry completeness/absence for the removed builders.
