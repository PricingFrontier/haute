# Codegen — High-Level Specification

## Purpose

Haute pipelines are authored visually as a React Flow graph (nodes + edges) but
executed as plain Python. Codegen is the one-way bridge from the visual
representation to human-readable Python: a standalone, re-runnable `.py` file
for a flat graph, or a small parseable file tree for a graph with submodels. The
valid-Python mutation and classification boundary is fixed by the accepted
[structured syntax decision](structured-syntax-boundary.md). Given a validated
`PipelineGraph`, it produces source that:

- Imports `haute` (and `polars` when the module refers to it), constructs a
  `haute.Pipeline`/`haute.Submodel` object, and defines one decorated function
  per node (`@pipeline.<type>` / `@submodel.<type>`). The decorator says what
  the node does and where its settings live; the function body holds only code
  the user wrote. A node without user code is a one-line declaration.
- Wires nodes together with `pipeline.connect(...)` calls that mirror the
  graph's edges, including multi-frame port names.
- Embeds enough metadata (docstrings, decorator kwargs, JSON config sidecars,
  column contracts where they add information, preserved free-form blocks)
  that the file can later be parsed back into an equivalent graph — see
  [pipeline-config](../pipeline-config/high-level.md) for the sidecar format
  and the parsing counterpart in `src/haute/parser.py`.
- Reads like a formatted, hand-written Python file: it is a fixed point of
  `ruff format` at its defaults and carries no generated scaffolding (no
  loader calls, no private imports, no empty docstrings, no module
  infrastructure).

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
  `haute._registry.NODE_REGISTRY`. A builder describes its node (decorator
  keywords, parameters, body) rather than printing it.
- Deciding whether a node's column contract adds information and, if so,
  rendering it as the decorator's `contract=` keyword (`_format_contract_kwarg`
  in `src/haute/codegen.py`).
- Laying the module out the way `ruff format` would (`haute._source_layout`,
  a small document printer shared with the Polars step renderer).
- Extracting the user-authored portion of a node's code editor content back
  out of a function body (`haute._code_extraction`): the docstring, the
  appended `return df`, the transform output declaration and the incomplete
  placeholder are the only generated parts left to strip.
- Low-level AST/source utilities shared with the parser
  (`haute._ast_helpers`): docstring stripping, dedent, decorator inspection,
  preamble/preserved-block extraction.
- The final "does it even parse" gate (`_assert_emitted_files_parse`).

Out of scope (owned by neighbouring components):

- Orchestrating emitted `.py` files back into a `PipelineGraph` —
  `src/haute/parser.py` and
  `src/haute/_parser_submodels.py` are owned by
  [expression-parsing](../expression-parsing/high-level.md). Parsed node/config conversion in
  `src/haute/_graph_builders.py` is owned by
  [pipeline-config](../pipeline-config/high-level.md). Codegen shares
  `src/haute/_ast_helpers.py` with those read paths, and `src/haute/_code_extraction.py`
  with the execution builders, the config builder and the assistant tools, because
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
  sort (`haute._topo.topo_sort_ids`) in which every node without inputs that feeds another
  node is placed immediately before its first consumer, so a data source is read where it
  is used rather than at the top of the file; a node without inputs or consumers keeps
  its topological position. Contract dicts and column sets are sorted, while connect
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
  sanitised public output port name (independent of alias or port count), and every ordinary
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
  file itself always names the frame each parameter binds. The one exception
  is a configured node carrying code (see "Declarations and code"): its first
  parameter is `df`, the frame the node's configured work produced, standing
  in place of the first input.
- **Submodel-aware.** A graph with no submodel occurrences produces exactly
  one file. A hierarchical graph emits each referenced definition file once,
  in first-occurrence order, plus a main file with one explicit
  `pipeline.submodel(path, name)` registration per occurrence
  (with `instance_of=owner_name` for copies). Distinct definitions may not share
  a file, unused registry definitions are rejected, and occurrence ids and
  aliases are never inferred. Parent connections name declared public port
  names; `in__<name>`/`out__<name>` exist only in graph JSON and are not
  emitted as authored parameter names. Inside a definition, child
  parameters use sanitised public input port names. Downstream, each output port
  contributes its sanitised public port name as the executable input name,
  independent of the occurrence alias; public ports declare a single
  canonical name: portId and label are not emitted.
  `graph_to_code` refuses a hierarchical graph rather than returning an
  arbitrary file. Each definition file carries its declared
  `definition_id`, complete literal `input_ports`/`output_ports` contract,
  description, preamble, and preserved blocks. Unused declared outputs
  therefore survive parse/save/reload without inference from parent edges.
- **Declarations and code.** Every node type except `polars` is a *configured
  node*: its behaviour comes from its settings, which live in a JSON sidecar
  named by the decorator's `config=` keyword (inline decorator keywords for
  Edge Join and Explore), and the decorator itself performs that behaviour when
  the file runs on its own (see [pipeline-config](../pipeline-config/high-level.md)).
  Codegen therefore emits no loader, scoring, join, switch or assembly call:
  - A configured node without user code is a **declaration**,
    `def <name>(<inputs>): ...`. Its parameters name its inputs, carry no
    annotations and no return annotation, and the body is `...`, or the
    docstring alone when the node has a description. The function is never
    called.
  - A Data Input, Rating Step, Model Score, Scenario Expander or Explore node
    that carries code or steps is a **hook**,
    `def <name>(df: pl.LazyFrame, <other inputs>: pl.LazyFrame) -> pl.LazyFrame:`.
    `df` is the frame the configured work produced from the first input (a
    Data Input's `df` is its loaded data, and it has no other parameters); the
    body is the user's code followed by `return df`.
  - An External File node that carries code or steps keeps every input as a
    parameter, receives the loaded object as the keyword-only `obj`, and binds
    `df = <first input>` as its first statement, the names its code box
    documents: `def <name>(<inputs>: pl.LazyFrame, *, obj) -> pl.LazyFrame:`.
  - API Input, Data Output, Edge Join, Banding, Output, Live Switch,
    Modelling, Optimiser, Optimiser Apply and Constant nodes never carry code
    and are always declarations.
  An input named `df` on a node whose type accepts code is a `ConfigError`,
  because a declaration whose first parameter is `df` would read as a hook.
- **Config-backed decorators.** A node type with a declarative JSON sidecar
  (`haute._config_io.has_config_folder`) has its builder emit
  `@pipeline.<decorator>(config="config/<type>/<name>.json")` through one
  shared helper: the decorator carries only the sidecar's path (and a contract
  when it adds information), never the config's contents. The config content
  itself is written separately by the config-io save path. A config-backed type
  without a registered decorator is a `HauteError`; codegen never defaults to a
  generic decorator. The module carries no config-base binding or other
  infrastructure: a decorator resolves its sidecar path against the pipeline's
  directory, the directory of the file that defines the function. A submodel
  definition file below its pipeline instead records the way back as the last
  keyword of its constructor, `pipeline_dir=".."` (one `..` per folder of its
  registration path), and the parser checks that value against where the file
  sits.
- **Canonical data I/O generation.** `dataInput` emits
  `@pipeline.data_input(config="config/data_input/<name>.json")` on a
  zero-parameter declaration, or on a `df` hook holding the post-load Polars
  code. `dataOutput` emits
  `@pipeline.data_output(config="config/data_output/<name>.json")` on a
  declaration: persistence happens only through the explicit output-write
  execution surface, never on import or an ordinary `Pipeline.run()`. Removed
  `dataSource`/`dataSink` forms are neither emitted nor accepted as codegen
  node types.
- **Sidecars stay live.** Nothing from a sidecar is copied into source: not a
  data path, schema, file type, model class, live-switch mapping or constant
  value. A sidecar-only edit therefore changes the next standalone run exactly
  as it changes canvas execution, and a malformed sidecar raises the same
  validation error in both.
- **Column contracts only where they add information.** A node's `contract=`
  keyword is emitted only when it tells the parser something the parser cannot
  derive offline from the node's configuration: a concrete input or output side
  that `resolve_parse_time_contract` leaves opaque (a Model Score's feature
  columns, a declared transform contract), or declared `inputs_by_parent`
  fan-in metadata. An opaque contract, or one that only repeats what the node's
  settings already imply, is omitted; an absent contract and `"opaque"` mean the
  same to every consumer. The contract is re-derived from the current config on
  every generation: parsing carries a previously generated annotation back onto
  `config["contract"]`, where it only supplies the sides the builder cannot
  derive plus its fan-in metadata, and a concrete builder side replaces it.
  Editing a node's config (renaming a Model Score output column, a banding
  output) therefore saves the refreshed annotation instead of a stale one the
  post-save parse check rejects. The keyword is rendered with the rest of the
  decorator; no generated source is edited after the fact.
- **Instance mappings are persisted.** An instance is a declaration whose
  decorator carries `of=...` and its explicit `inputMapping=...`. Parsing
  therefore restores the mapping metadata needed for a later graph edit or save
  instead of relying on name heuristics after the first reload.
- **Polars transforms bind inputs by name only.** A `polars` node's function
  parameters are its logical inputs, one per incoming edge in edge order; `df` is
  purely the output variable and is never pre-bound to an input. The body is the
  user's code and the appended `return df`. When the code binds `df` nowhere,
  the body first declares `df: pl.LazyFrame` as an unbound local, so a
  same-named preamble global cannot satisfy the missing output assignment.
  Normally the logical name is the edge-derived
  name; a non-instance Polars `inputMapping` may preserve an earlier logical
  name across a structural rewrite. Mapping values must match distinct current
  edge names and the resulting logical names must remain distinct valid Haute
  identifiers, otherwise generation and execution fail loudly. The user starts
  from the input they mean by name and assigns the result to `df`. Configured
  nodes with code keep their implicit `df` frame (see "Declarations and code");
  only `polars` transforms carry the named-input contract.
- **User code round-trips.** Text typed into a node's code editor is the
  function body, followed by the appended `return df` (and, for an External
  File, preceded by its `df = <first input>` binding). On the next save
  `haute._code_extraction` removes only those generated statements, the
  docstring, the transform output declaration and the incomplete placeholder,
  so repeated edit/save cycles neither accumulate scaffolding nor lose the
  user's formatting and comments.
- **Formatted like a hand-written file.** The emitted module is a fixed point of
  `ruff format` at its defaults (88 columns, double quotes, magic trailing
  commas): the constructor, decorators, signatures, submodel registrations and
  connect calls are laid out by `haute._source_layout` the way ruff lays them
  out; strings take double quotes unless single ones need fewer escapes; the
  imports are `import haute` then `import polars as pl`, the latter only when
  the module refers to `pl`; and blank lines follow ruff's rules. A docstring is
  emitted only for a non-empty description, except that a body with code whose
  first statement is itself a string keeps an empty docstring so that string
  stays code. Declarations carry no annotations, so a type checker does not
  report their `...` bodies as missing return values. Only authored text
  (preamble, preserved blocks, user code) can make a saved file differ from
  ruff's layout.
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
  produce invalid Python — a missing codegen builder, an invalid description or
  literal, an unparseable emitted file — raises rather than degrading to a
  partial or passthrough result. See Failure model.

**Stepped transforms.** When a transform config carries `steps`, `_gen_transform` renders
the body with the shared step renderer against the generated parameter names instead of
`config["code"]`, references the node's `config/polars/<name>.json` sidecar through the
decorator `config=` keyword, and on a render failure emits the same incomplete placeholder
body that a code-less transform emits; the save warning for incomplete transforms then
names the failing step. A stepped original that also carries `inputMapping` is rejected
with a `ConfigError`. Generating, parsing, and extracting a stepped transform reproduces
the rendered code exactly, which is what lets the parser tell a hand-edited body from a
rendered one; the parser compares the two as programs (the same syntax tree and
comments, against both the current rendering and the renderer's earlier call spelling
of the same steps), so a body in an earlier layout, quoting or spelling of the same
steps is not an edit.

**Stepped frame surfaces.** When a Data Input, External File, Rating Step, Model Score,
Scenario Expander or Explore config carries `steps`, the node is a hook and its generator
renders the steps in frame mode against the surface's eligible input names (every edge
name for an External File, the empty list otherwise). The rendered lines are the hook's
body, after the `df = <first input>` binding for an External File and before
`return df`; an Explore node's step list is emitted as a `steps=` decorator argument,
not a sidecar. When the steps cannot be rendered the hook's body is the raising
placeholder instead, carrying the constant `INCOMPLETE_STEPS_MESSAGE`, so a standalone
run of the module raises rather than running the node's configured work unchanged; the
placeholder recogniser accepts either constant and extraction treats a recognised
placeholder as generated, so the body reloads as empty code with the steps kept. The
parser does not require extraction to be a fixpoint of rendering: it compares the
extracted body with the rendering passed through the same finaliser extraction ends
with.

## Design rationale

- **Configured behaviour lives in the decorator.** Canvas execution, preview, trace and
  deploy never ran generated function bodies: the executor builds each node from its
  parsed configuration. The bodies existed only so a standalone `Pipeline.run()` could
  repeat that work, which cost every saved file its loader imports, duplicated config
  paths, private helper calls and module infrastructure. Performing the work in the
  decorator removes all of it without changing what either path runs, and leaves the
  function body as the one place user code lives.
- **Structured generation and one layout routine.** Builders describe a node — decorator
  keywords, parameters, docstring, body — and one printer lays the module out the way
  `ruff format` would (the Wadler/Prettier document algorithm ruff itself follows). Saved
  files are therefore diffable, lint-clean and stable under the project's formatter, and
  there is no post-generation source edit: a contract is rendered with the rest of its
  decorator. String safety for interpolated text remains explicit in the literal printer
  and `_sanitize_description`.
- **One proven shared node declaration, not inferred parity.** The modelling
  node's first-connected-input passthrough policy and decorator config keys are
  declared once in `haute._registry` and consumed by both the executor builder and the
  standalone runtime. Other node types retain explicit builders until a direct
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
  "offline," not fallback-worthy.** `_is_codegen_infra_error` narrowly
  allowlists environmental failures (missing artifact, unreachable MLflow
  server) so codegen can save a pipeline in a disconnected/CI environment
  without a running model server. Such a failure degrades the annotation to
  the contract the parser derives offline (for Model Score: the configured
  output column, with inputs opaque unless declared), which is exactly what
  the post-save parse check compares it against. Every other exception —
  misconfiguration, a genuine bug in contract computation — propagates and
  fails the save; masking it by omitting the contract would hide a real
  defect inside a file that then runs and fails far from the cause.
- **Return stripping is AST-based, not line-based.** Determining which
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
  node types emit a `config=` reference as their decorator.
- **Depends on** `haute._registry.NODE_REGISTRY` as the single source of
  truth for which builder handles which `NodeType`; a missing codegen entry
  is a registry wiring bug, not something codegen falls back for.
- **Depends on** `haute._graph_shape`, `haute._edge_join`, and
  `haute._topo` for graph-shape validation, edge-join role resolution, and
  topological ordering before any source is emitted.
- **Shares** `src/haute/_ast_helpers.py` with the parser
  (`src/haute/parser.py`, `src/haute/_graph_builders.py`,
  `src/haute/_parser_submodels.py`) and `src/haute/_code_extraction.py` with
  `src/haute/_builders.py`, `src/haute/_config_builder.py` and
  `src/haute/assistant/_tools.py` — generation and extraction
  are two halves of one round-trip contract; a change to how codegen wraps
  user code generally requires a matching change to how extraction unwraps
  it.
- **Shares** `src/haute/_source_layout.py` with the Polars step layout
  (`src/haute/_polars_steps_layout.py`, owned by
  [pipeline-config](../pipeline-config/low-level.md)): one document printer
  lays out both the module and the rendered step statements.
- **Relies on** the standalone runtime in `src/haute/pipeline.py` and
  `src/haute/_standalone_nodes.py` (owned by
  [pipeline-config](../pipeline-config/high-level.md)) to perform each
  configured node's work, so a declaration or hook runs on its own.
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
- **A config-backed node type has no decorator mapping** → `HauteError` with
  the node id, label, and type. Codegen never substitutes `@pipeline.polars`.
- **An input named `df` on a node whose type accepts code** (Data Input,
  External File, Rating Step, Model Score, Scenario Expander, Explore) →
  `ConfigError` naming the node: a declaration whose first parameter is `df`
  would read as a hook. Rename the upstream node or frame.
- **A decorator keyword value that has no Python literal form** (anything but
  strings, numbers, booleans, `None`, lists and string-keyed dicts of those) →
  `HauteError` from the literal printer; codegen never falls back to `repr`.
- **Contract computation raises `ConfigError`** (user misconfiguration) or
  any other non-infra exception → propagates unchanged; only `OSError` and
  `mlflow.*` exceptions are downgraded to the offline parse-time contract.
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

## Model families in generated code

Generated training scripts build every family's job through the shared training configuration
(`build_training_job_kwargs`), so a script and a canvas run share configuration, training
identity and effective parameters, `positive_class` included. A generated Model Score node is
suffix-agnostic: its decorator scores through `score_from_config`, which selects the configured
artifact, and the loader dispatches `.cbm`, `.rsglm`,
`.ubj`, `.lgbm` and `.ebm` to their flavors, so an XGBoost, LightGBM or EBM model scores
through the same adapter as the GUI; an EBM also needs the feature contract saved beside it.

