# Codegen — Low-Level Specification

## Authored configuration preservation

- Persistence assurance covers the complete browser edit/save/reload path,
  generated edit/undo/redo/save sequences against an independent state model,
  delayed preview/save/watcher responses, and shared submodel definitions plus
  independent occurrences. Assertions compare authored settings, dirty/history
  state where applicable, and resulting data rather than only successful calls.
- Every declared persisted configuration field must be represented by an explicit
  round-trip example or a documented non-persisted/structural field policy. Adding
  a declared field without extending the inventory fails the coverage gate.
- Mutation witnesses deliberately remove persisted settings or overwrite newer
  state and must be rejected by the same assertions used for healthy results.
  They run without modifying production source files.

- Every executable node type preserves the shared `selected_columns`,
  `column_renames`, and `categorical_levels` settings through graph -> generated
  source plus sidecars -> parsed graph -> repeated save. Inline nodes (Polars,
  Edge Join, Explore) use the same shared field definition as sidecar validation.
  Empty column selection may canonicalize to absence; both mean all columns.
  Non-empty selections retain their order, and mapping keys/values remain exact.
- JSON serialization removes underscore-prefixed editor properties only at
  defined configuration-record boundaries. Arbitrary user mappings and data
  payloads are opaque: column names, category labels, model parameters, input
  records, nested schemas, and rating-table row keys may start with underscores,
  including names that coincide with editor properties such as `_id`.
- Nested transient banding properties on factors and expanded rule records are
  removed without mutating the input graph. Compact categorical rule mappings
  are user data, not editor records, and retain every category key.
- Regression coverage exercises shared settings across every non-container node
  type, absent/empty/populated settings, unusual names, sidecar and inline
  storage, repeated saves, and execution of representative reloaded pipelines.
  Property tests must generate underscore-prefixed user names rather than
  excluding inputs that expose serialization defects.
- Repository safety checks discover tracked and unignored Python source files
  through Git, including new files awaiting commit. They do not scan local
  caches or temporary projects, and discovery runs during the check rather
  than while importing the test module.

## Module map

| File | Responsibility |
|---|---|
| `src/haute/codegen.py` | Public orchestration API (`graph_to_code`, `graph_to_code_multi`); single-node dispatch (`_node_to_code`, `_generate_node_code`, `_render`); instance-node handling (`_instance_to_code`); the contract decision and value (`_contract_keyword`, `_contract_value`); node emission order (`_emission_order`); module assembly (`_render_module`); the final parse gate (`_assert_emitted_files_parse`). |
| `src/haute/_codegen_builders.py` | One `_gen_*` builder per `NodeType`, registered into `haute._registry.NODE_REGISTRY` via `@_register_codegen`; each returns a `NodeSource` rather than text. Shared helpers build a configured node's declaration or hook (`_configured_node`), the config-backed decorator keywords (`_config_keywords`), parameters (`_params` — the per-edge input names supplied by the orchestrator; duplicates are rejected by `src/haute/codegen.py::_validate_duplicate_node_inputs`) and the transform body (`_transform_body`, which adds the output declaration only when the code binds `df` nowhere). `render_node_source` prints a `NodeSource` through the shared document printer; `_sanitize_description` and `_docstring_lines` prepare docstring text. |
| `src/haute/_source_layout.py` | The document printer both codegen and the Polars step layout use: the Wadler/Prettier document (`Group`, `Indent`, `Line`, `IfBreak`), `print_doc`, string quoting as ruff writes it (`quote_string`), collections laid out one entry per line when broken (`bracketed`), call and signature arguments laid out as ruff lays them out (`arguments`: flat, then on one indented line, then one per line with a trailing comma; a broken signature's lone parameter also takes the comma, a lone call argument never), and the literal printer (`literal`) for decorator values. |
| `src/haute/_python_syntax.py` | Formatting-preserving valid-Python boundary: exact method-call discovery and exact expression/function replacement, with stable structured syntax failures. It never repairs invalid Python syntax or evaluates source. Codegen no longer edits generated source, so it does not use this module. |
| `src/haute/_registry.py` | Cross-component dependency owned by [pipeline-config](../pipeline-config/low-level.md): codegen registers and reads per-node code builders through the canonical registry. |
| `src/haute/_code_extraction.py` | Reverse direction of the builders: recovers the user-authored code from a function body. Three kinds (`polars`, `hook`, `external`) share one engine (`extract_user_code`): strip the docstring, recognise a declaration body (`is_declaration_body`: nothing but `...` or `pass`), strip the transform output declaration or the External File `df = <first input>` binding, recognise the incomplete placeholder, strip the trailing `return df`, and finalise with the Polars rules. |
| `src/haute/_ast_helpers.py` | Stateless AST/source utilities with no node/graph knowledge: literal evaluation (`_eval_ast_literal`), decorator introspection (`_get_decorator_kwargs`, `_is_pipeline_node_decorator`, `_get_decorator_node_type`), docstring/whitespace handling (`_strip_docstring`, `_dedent`), and whole-file extraction helpers (`_extract_function_bodies`, `_extract_connect_calls`, `_extract_meta`, `_extract_preamble`, `_extract_preserved_blocks`) shared with the parser. |

## Key types and data structures

- **`NodeSource`** (`_codegen_builders.py`) — frozen dataclass: `decorator` (the
  registry method name, e.g. `data_input`, `polars`, `instance`), `keywords`
  (ordered `(name, value)` decorator keywords whose values are literals),
  `params` / `keyword_only` (`Param(name, annotation)` tuples), `returns`
  (`"pl.LazyFrame"` or `None`), `description`, and `body` (`None` for a
  declaration, otherwise the unindented body text). It is what a builder says
  about its node; `render_node_source(source, receiver=...)` prints it, adding
  the contract keyword the orchestrator decided on.
- **`_ConnectPair`** (`codegen.py`) — `tuple[str, str, str | None, str | None]`: `(src_func, tgt_func, source_port, target_port)`. `source_port`/`target_port` are `None` for the bare `connect("a", "b")` form used by ordinary single-output sources. Every `apiInput` edge — including one from a sole-frame source — carries its frame label as `source_port`, so the generated file always names the frame each connection delivers; a bare connect from an `apiInput` is not emitted.
- **`CodegenBuilder`** (`_codegen_builders.py`) — `Callable[[GraphNode, list[str]], NodeSource]`; the signature every `_gen_*` function implements. Registered per `NodeType` into `NODE_REGISTRY[node_type].codegen` (see `haute._registry`); `NODE_REGISTRY` pairs each type's codegen builder with its exec-side runtime builder from `haute._builders`, and `validate_registry_complete` enforces both are present for every type.
- **Document nodes** (`_source_layout.py`) — `str | Group | Indent | Line | IfBreak | list`:
  a `Group` prints flat when it fits on the line up to the next possible break and
  broken otherwise; a broken `Line` is a newline at the current indent (flat: a space,
  or nothing when soft); an `IfBreak` prints only inside a broken group (the magic
  trailing comma).
- **`MatcherResult`** (`_code_extraction.py`) — `NamedTuple(start_idx: int, return_vars: tuple[str, ...])`: the first line of the cleaned body that is user code, and the variables whose trailing `return <var>` is generated.
- **`_UserCodeParseError`** (`_code_extraction.py`) — multiple-inherits `ParseError` (Haute's error hierarchy) and `ValueError`; raised by `_parse_user_code` when extractable text isn't valid Python, chaining the original `SyntaxError`.

## Control flow

### `graph_to_code_multi` (the real entry point; `graph_to_code` wraps it)

1. Fall back to `graph.pipeline_description` and `graph.preamble` when the
   corresponding explicit value is empty; default
   `submodels = graph.submodels or {}`.
2. `validate_pipeline_graph_shape_contracts` — structural preflight (owned by
   `haute._graph_shape`), run before any source is generated.
3. Resolve and validate canonical definitions and occurrences, reject
   unreferenced definitions and shared-file collisions, then run
   `_error_on_name_collisions` over root nodes, submodel occurrence aliases,
   plus each referenced definition graph exactly once.
4. **No-submodel path:** order edges (`_order_edge_join_incoming_edges` puts
   each edge-join's two incoming edges in base-then-join order), topo-sort
   nodes (`_topo_sort` via strict `haute._topo.topo_sort_ids`, which raises
   `UnknownEdgeEndpointError` with dropped-edge evidence for any dangling endpoint) and
   place each input-less node that feeds another immediately before its first consumer
   (`_emission_order`), build
   id→func-name maps and each node's per-edge input-name list
   (`edge_input_name(edge, source_node)` in edge order — the same list the
   executor derives, so signature and binding can never disagree), raising
   `ParseError` on a duplicate input name within one node (via the shared
   `duplicate_input_names` detector in `_graph_utils.py`), build
   `connect_pairs` directly from edges, call
   `_render_module(kind="pipeline", ...)`, then
   `_assert_emitted_files_parse` on the single resulting file.
5. **Submodel path:** resolve every canonical occurrence and order definitions
   by first occurrence. For each definition, order its internal graph the same way,
   derive child parameters from structured public input targets followed by
   internal edges, validate every structured output source, and emit one
   `haute.Submodel(..., definition_id=..., input_ports=...,
   output_ports=...)` file, ending with `pipeline_dir=` when the definition's
   file sits below the pipeline (`_submodel_paths.definition_pipeline_dir` of
   its registration path: one `..` per folder). Then omit occurrence nodes from the root function
   list, translate parent boundary handles only to declared public port names,
   derive child-boundary parameters from sanitised public input port names and downstream names from sanitised public output port names, and emit one explicit
   `pipeline.submodel(path, name)` registration per occurrence (with `instance_of=owner_name` for copies). Parent `connect` calls refer to aliases
   plus public port names; synthetic `in__`/`out__` handles never enter source.
   Finally `_assert_emitted_files_parse` validates every emitted file.

### `_render_module` (shared by both paths above)

Builds the file as a sequence of blocks separated the way `ruff format`
separates them: the docstring header (name run through `_sanitize_description`
since it lands between the module docstring's triple quotes), one blank line,
the imports, one blank line, the optional per-file preamble, the
`Pipeline(...)`/`Submodel(...)` construction (an empty `description` is
omitted), any preserved blocks (`_emit_preserved_blocks`, wrapped in
`# haute:preserve-start/-end` markers), then each node function preceded by two
blank lines (original nodes in emission order, then instance nodes), the
submodel registration calls, and the `pipeline.connect(...)`/
`submodel.connect(...)` calls under the `# Wire nodes together` comment
(deduped when `dedup_connects=True`). The module ends with exactly one newline.
The imports are `import haute` followed by `import polars as pl`; the second is
emitted only when the rest of the module refers to the name `pl` (checked on
the parsed module, so a preamble that uses `pl` keeps it).

Every call and signature is printed through `haute._source_layout`: the
constructor, decorators, `def` lines, submodel registrations and connect calls
print flat when they fit in 88 columns, otherwise with their arguments on one
indented line, otherwise one argument per line with a trailing comma (as ruff does,
a lone parameter of a broken signature takes the comma too and a lone call argument
never does); a list or
dict value that does not fit puts one entry per line with a trailing comma.
Strings take double quotes unless single ones need fewer escapes. The result is
a fixed point of `ruff format` at its defaults.

`_emission_order(sorted_nodes, edges)` keeps the topological order of every node
that has inputs and moves each input-less node that feeds another to just before
its first consumer, emitting the sources of one consumer in that consumer's edge
order; an input-less node without consumers keeps its topological position.

Preserved-block extraction is intentionally structural rather than byte-for-byte. The shared
`haute._ast_helpers._extract_preserved_blocks` scan claims only completed column-zero marker
pairs, removes marker lines and leading/trailing blank lines inside each block, ignores an
unmatched start, and returns blocks in source order. `_extract_preamble` excludes those completed
module spans, so the two stores are disjoint. Indented marker text remains in its enclosing
function or construct and is not separately extracted. `_render_module` emits each
pipeline or submodel block once after object construction and before node functions, restoring
fresh markers.

### `_node_to_code` (per-node dispatch)

1. `_order_edge_join_incoming_edges` runs before per-node dispatch and orders
   each Edge Join's physical incoming edges base-then-join from their
   `targetHandle` values. It requires exactly one `base` and one `join` handle.
2. `_node_functions` passes the already edge-derived `source_names` to
   `_generate_node_code`; its parallel `source_ids` are used only to attribute
   column-contract parent names and never to resolve input roles or selectors.
3. `_generate_node_code` — looks up `NODE_REGISTRY[node.data.nodeType].codegen`
   and calls it; raises `KeyError` if either the entry or its codegen builder
   is missing. The builder returns a `NodeSource`.
4. A config-backed builder (`has_config_folder(node_type)`, from
   `haute._config_io`) takes its first keyword from `_config_keywords(node,
   func_name)`: `config=<path>` from `config_path_for_node`, with the decorator
   name from the complete `NODE_TYPE_TO_DECORATOR` mapping; a missing mapping
   raises `HauteError` with node context rather than defaulting. No config value
   is rendered into the decorator. The rating-step builder still validates the
   table and combined-output shapes, because codegen runs at save and a
   malformed config must fail there as it would at execution; the optimiser and
   optimiser-apply builders validate their input selectors for the same reason.
5. `_contract_keyword` decides the contract keyword. It derives the builder
   contract from the current config (`_derive_contract_for_codegen`) and fills
   only that contract's opaque sides from a declared `config["contract"]`
   (`Contract.fill_opaque_sides`), keeping the declaration's `inputs_by_parent`;
   an instance, or `"declared"` generation, starts from the declaration alone. It
   then compares the result with what the parser derives offline
   (`resolve_parse_time_contract`) and returns `None` — no keyword — unless the
   result has a concrete side the offline derivation leaves opaque or carries
   `inputs_by_parent`. `_node_to_code`'s `contract_source` picks the derivation:
   `"builder"` (the default, used by save) derives through
   `_derive_contract_for_codegen`; `"offline"` (recovery generation) derives
   what the parse-time check compares against, so it never loads an external
   model artifact; `"declared"` derives nothing.
6. `render_node_source` prints the decorator (bare when it has no keywords, the
   contract keyword last) and the function, with the receiver `pipeline` or
   `submodel` chosen by the file kind.

### Configured nodes: declarations and hooks

Every builder except `_gen_transform` describes a configured node.

- **Declaration** (`_configured_node`, and `_instance_to_code` for an instance): no user code. Parameters are the node's
  input names without annotations, there is no return annotation, and the body
  is `...` — or the docstring alone when the node has a description, since a
  docstring is already a complete body. API Input, Data Output, Edge Join,
  Banding, Output, Live Switch, Modelling, Optimiser, Optimiser Apply and
  Constant nodes are always declarations; so is an instance node, whose
  decorator carries `of=` and its `inputMapping=`.
- **Hook** (`_configured_node`): a Data Input, Rating Step, Model Score, Scenario Expander or
  Explore node whose config carries code or steps. The first parameter is
  `df: pl.LazyFrame` in place of the first input (a Data Input has only `df`),
  the remaining inputs follow as `name: pl.LazyFrame`, the return annotation is
  `pl.LazyFrame`, and the body is the user's code — or the rendered steps —
  followed by `return df`. Steps that cannot be rendered give the
  `INCOMPLETE_STEPS_BODY` placeholder as the whole body.
- **External File hook**: every input stays a parameter by name, the loaded
  object arrives as the keyword-only `obj`, and the body begins with
  `df = <first input>` before the code and the closing `return df`. A
  disconnected External File with code has no positional parameters and no
  binding line.
- The code-accepting builders raise `ConfigError` for an input named `df`.
- Removed `dataSource`/`dataSink` enum values, decorators, templates, and
  extractor aliases have no compatibility path. Round trips preserve the
  retained I/O provider branch, format/mode, arguments, destination fields,
  connections, and user code without inventing inactive fields. No cache-mode
  field exists to round-trip; execution mode is derived.

The decorator performs the configured work when the module runs on its own; the
table of what each type does, and the registration checks that keep declarations
and hooks honest, belong to [pipeline-config](../pipeline-config/low-level.md).

### `extract_user_code` (the extraction engine)

1. Trim leading/trailing blank lines from `body_source`, then
   `_strip_docstring` (AST-based: wraps the body in a synthetic `def _f():`
   so line numbers are recoverable, parses, and slices past
   `ast.get_docstring`'s end line — never a textual triple-quote scan,
   because escaped quotes inside the docstring content defeat that).
2. A body that is nothing but `...` or `pass` (`is_declaration_body`) has no
   user code.
3. Look up the `kind`'s matcher (`KeyError` if `kind` is unknown): `polars`
   skips the exact unbound output declaration `df: pl.LazyFrame`; `external`
   skips a first statement `df = <first positional parameter>`; `hook` skips
   nothing. A recognised incomplete placeholder at that position is generated
   too.
4. Slice from `result.start_idx`, `_dedent`, strip a trailing
   `return <var>` for each `return_vars` entry via `_strip_trailing_return`
   (AST-based — `_strip_outer_trailing_return` only removes the return if it
   is the literal last OUTER-scope statement).
5. Finalise through `_finalise_polars`, with the parameter names for `polars`
   and none for the other kinds.

`normalise_user_code(code, kind=...)` applies only step 5, so the parser can
compare a step rendering with an extracted body on equal terms.

### The polars named-input contract

A `polars` transform's function parameters ARE its inputs: each incoming edge
binds one parameter (`edge_input_name`, in edge order), and `df` is only the
node's OUTPUT variable. For a non-instance transform carrying
`inputMapping={logical_name: current_edge_name}`, `_gen_transform` validates a
one-to-one mapping against the current edge-derived names, emits the logical
names in the same edge order, and persists the mapping as a decorator kwarg.
The parser copies that kwarg back into node config and uses each mapped current
name when reconstructing implicit edges from the logical function parameters.
It must not also infer an edge from a same-named node that happens to match the
logical parameter. This makes graph → source → graph a fixpoint after an Edge
Join (or another topology rewrite) replaces a parent: authored code keeps
reading the original logical input while both the canvas executor and generated
positional call receive the replacement parent's frame. Stale values,
duplicate current values, invalid logical identifiers, or logical-name
collisions are `ConfigError`s rather than guessed bindings.

A transform with no incoming edges therefore has an
empty parameter list — never a phantom default `df` input — and an incoming
edge whose derived name is literally `df` is rejected as a reserved-name
collision once executable code is present, rather than weakening the
output-only contract. A no-code half-built node still saves with its ordinary
raising placeholder. For executable code, `_gen_transform` emits the user code
verbatim and the appended `return df`. When the code does not itself make `df`
a name of the function (`_code_makes_df_local`, which reads Python's own scoping
through `symtable`, so every binding form and a `global df` declaration count),
the body first declares `df: pl.LazyFrame`: the declaration creates a
function-local output slot without binding a value, so a preamble global named
`df` cannot mask the missing assignment. Code that binds `df` anywhere already
makes it local, so the declaration would add nothing. User code must start from the input it means by
name (`df = quotes.join(regions, ...)`), and reading `df` before assigning it is
a `NameError` at run time, in the generated module and canvas execution alike.

A stepped transform (`config["steps"]` is a list) has its body rendered by
`_polars_steps.render_polars_steps` against the logical parameter names, one
statement per step (over several lines when it does not fit in 88 columns at the body's indentation) with a
leading `df = <input>`, and its decorator carries
`config="config/polars/<func>.json"` so the parser reloads the steps. A render
failure emits the incomplete placeholder body (the save warns which step is
incomplete); `steps` together with `inputMapping` on an original is a `ConfigError`.
A stepped frame surface (`config["steps"]` is a list on a Data Input, External File,
Rating Step, Model Score, Scenario Expander or Explore) is a hook whose body is
produced by `_stepped_body_code(config, node_type, source_names)`: the rendering by
`render_polars_steps(steps, step_input_names(node_type, source_names), start="frame")`,
or `incomplete=True` when they cannot be rendered, in which case the hook's body is
the `INCOMPLETE_STEPS_BODY` placeholder (after an External File's binding line). The
steps live in each type's required sidecar, except Explore's, which `_gen_explore`
appends to its decorator keywords as `steps=[...]` and `_build_node_config` reads back
from the decorator kwargs.
A node with NO code cannot run at
all — there is no implicit single-input passthrough; codegen emits the
`NotImplementedError` placeholder and the executor installs the matching
raising callable. Extraction is symmetric: the
`polars` matcher strips only the exact unbound output declaration, while its
finaliser treats a leading `df = <param>` line as authored code, never as
strippable scaffold, and does not collapse a lone `return <param>` body to
empty code.

## Edge cases and invariants

- **Multi-edge into one node** (the same upstream `apiInput` feeding a node
  through two frame edges) — each edge contributes its own frame label as the
  parameter name, so the parameters are distinct by the api-input schema's
  label-uniqueness rule; no suffixing exists. A derived duplicate across
  *different* sources (frame label colliding with another input's name) is a
  `ParseError`, never a rename. A disconnected declaration or transform has an
  empty parameter list; a disconnected hook has `df` alone.
- **User-controlled text inside decorator keyword values** (a column literally
  named `"price (gbp)"`, or containing `":)"` or a quote) — the literal printer
  quotes every string itself (`quote_string`, escaping as ruff writes it), so
  such text is always one string literal; there is no textual insertion point
  for it to break.
- **Descriptions containing triple quotes, backslashes, or edge whitespace**
  — `_sanitize_description` doubles every backslash, escapes every `"`
  (preventing any run of 3+ quotes from closing the enclosing `"""` early),
  and prepends a `\n` when the description has newlines or leading/trailing
  whitespace (neutralising `inspect.cleandoc`'s indent-stripping behaviour
  so a round-trip through `ast.get_docstring` reproduces the original
  bit-for-bit). Continuation lines of a multi-line description are indented to
  the function body, as `ruff format` indents docstrings; `inspect.cleandoc`
  removes that common indentation again on read. Curly braces are left
  untouched: the value is never spliced into a format template.
- **`Contract.inputs_by_parent` stale keys** — a parent id present in the
  contract metadata but no longer connected after a UI rewire is *omitted*,
  not guessed at, logged via `contract_inputs_by_parent_omitted_stale`;
  edges/node bodies remain the source of truth for what's actually
  connected.
- **Two `inputs_by_parent` keys collapsing to the same emitted parent name
  with different column sets** — genuine ambiguity, raises `ParseError`
  rather than picking a "last writer."
- **`polars` transform with no code** — cannot RUN, whatever its input count:
  the node's output is whatever its code assigns to `df`, and there is no
  implicit passthrough (not even for a single input). Still an ordinary state
  for a graph still being built, so it never blocks a SAVE. `_gen_transform`
  emits `_code_extraction.INCOMPLETE_TRANSFORM_BODY`: a valid body that raises
  `NotImplementedError` if executed. It never silently passes one input through
  and drops the rest, and never emits `return df` where `df` is unbound. Save
  reports it through `_validate_transforms_are_runnable` as a non-blocking
  warning, alongside the empty-`tables[]` API Input warning.
  The placeholder's message is a CONSTANT naming no node or source, so
  extraction can recognise it and treat it as generated — the node
  round-trips back into the editor still empty. An interpolated message would
  leave nothing fixed to match on, and matching loosely (any leading
  `raise NotImplementedError`) would swallow a user's own first line on reload.
  The failing node is identified by the function name in the traceback.
  Recognition is **structural**, not textual
  (`_code_extraction._is_incomplete_transform_placeholder` compares the parsed
  statement). The emitted source is not what stays on disk: the generated `.py`
  is a real source file that editors, pre-commit hooks and `ruff format` touch,
  so quote style, line wrapping and the magic trailing comma all vary.
  A textual comparison silently stops matching after any such reformat, and the
  placeholder then returns as the user's own code — writing a `raise` into a
  node they deliberately left empty. Anything the user adds AFTER the
  placeholder is preserved; a `df = <param>` line
  anywhere in a polars body is authored code, never scaffold (see "The polars
  named-input contract" above).
  The live executor keeps the same invariant: a no-code polars node installs a
  callable that raises the same `NotImplementedError`, whatever its upstream
  count — there is no first-input passthrough. Save validation scans both the
  root graph and every embedded submodel definition, so every generated
  placeholder is named in a non-blocking warning.
- **Empty/cleared code box producing a degenerate `df = (\n)`** — parses as
  `df = ()` (an empty tuple), recognized by `_is_empty_chain_assignment` as
  leftover scaffolding and collapsed to empty user code, not left as
  literal invalid-looking-but-technically-valid Python.
- **Chain-assignment paren unwrapping is provably safe or not attempted at
  all** — `_strip_redundant_rhs_wrapper_once` only removes a `df = (...)`
  wrapper pair when dropping it and re-parsing yields an *identical* AST
  (`ast.dump` comparison); `df = (a + b) * c` and unbalanced splits like
  `df = (x.filter(...)).join(...)` both fail the proof and are left
  untouched.
- **`EDGE_JOIN` codegen dispatch bypassing role ordering** — `_gen_edge_join`
  itself re-validates `len(source_names) == 2`. Graph assembly validates exactly one `base` and
  one `join` target handle and orders the physical edges before building source names. The
  generated decorator contains join options only, on a declaration whose parameters are the
  base and join inputs in that order; explicit `connect(..., target_port=...)` calls
  preserve roles for parser reconstruction. Retired `base_input`/`join_input` decorator arguments
  and `baseInput`/`joinInput` config are rejected rather than migrated.
- **Cross-boundary edge-join role resolution at a submodel boundary** —
  `codegen._canonical_definition_source_metadata` collects each edge-join
  target's bindings (public input-port targets plus internal edges) and orders
  them base-then-join via `haute._edge_join.resolve_edge_join_role_indices`,
  since the join's base/join role isn't visible from the root-graph edge alone.
- **Windows-style paths in generated `config=` literals** — `config_path_for_node`
  builds sidecar paths with forward slashes, so a pipeline saved on Windows and read
  on Linux (or vice versa) still parses correctly. No emitted module contains a
  `__file__` expression; a decorator resolves its path against the file that
  defines it.
- **External File binding line** — the only generated statement before an External
  File hook's code is `df = <first input>`. Extraction strips it only when it is the
  first statement and names the first positional parameter; a later alias, or an
  import the user wrote first, is authored code.
- **Declarations are recognised structurally.** A body that is only `...` or `pass`
  (after an optional docstring) is a declaration wherever it is written, so a
  hand-formatted declaration parses as one. The only other generated statements are
  the docstring, the appended `return df`, the transform output declaration, the
  External File binding and the incomplete placeholder; extraction carries no
  aliases for retired generated chains or variable names. Rating-step codegen emits
  only canonical table fields and `combined_outputs`, never retired table labels or
  singular combined-output arguments.
- **Hierarchical main files are a static-parser artifact, not a live import mechanism** —
  `pipeline.submodel(path)` only appends the path to the live `Pipeline` object's
  `_submodel_files`; it does not import child decorators. `_assert_emitted_files_parse` proves the
  file tree is syntactically valid, while parser round-trip tests prove the static path. Direct
  execution equivalence is covered only for flat/single-file generated graphs.

## Error handling

| Condition | Exception | Raised from |
|---|---|---|
| No codegen builder registered for a `NodeType` | `KeyError` | `codegen._generate_node_code` |
| Config-backed node has no decorator mapping | `HauteError` with node id/label/type | `_codegen_builders._config_keywords` |
| A decorator keyword value with no Python literal form (not a string, number, boolean, `None`, list, or string-keyed dict of those; or a non-finite float) | `HauteError` naming the value's type | `_source_layout.literal` |
| Contract computation hits `OSError` or an `mlflow.*` exception | degraded, with a `contract_emit_offline_on_error` warning, to the offline parse-time contract (`_config_builder.resolve_parse_time_contract`); a Model Score annotation keeps any declared inputs, and the keyword is omitted when nothing the parser cannot derive remains | `codegen._derive_contract_for_codegen` |
| Contract computation hits `ConfigError` | `ConfigError` (propagated) — except the `MlflowDestinationUnconfigured` marker: a MODEL_SCORE node whose explicit `mlflow_destination` is merely not configured on the authoring machine is environmental, so the annotation degrades to the offline parse-time contract with a warning (the executor resolves the same destination at run time and fails loudly there); every other `MlflowConfigError` (unknown key, rejected SDK mode) still propagates | `codegen._derive_contract_for_codegen` |
| Contract computation hits a non-infra exception (`TypeError`, `KeyError`, `HauteError` incl. `ContractMismatchError`) | propagated unchanged | `codegen._derive_contract_for_codegen` |
| `inputs_by_parent` ambiguous key collision | `ParseError` | `codegen._format_contract_source` |
| Duplicate sanitized function names or occurrence aliases across root graph + submodels, including exact duplicate labels | `ParseError` (all colliding buckets listed) | `codegen._error_on_name_collisions` |
| Duplicate derived input names among one node's incoming edges | `ParseError` (target node + colliding input name) | `codegen.graph_to_code_multi` (per-edge input-name assembly) |
| An `apiInput` edge carrying no `source_port`/`sourceHandle` (only reachable via a hand-edited file — the editor cannot create one) | `ParseError` naming the edge and source node | `codegen.graph_to_code_multi` (per-edge input-name assembly) |
| `edgeJoin` incoming edges do not carry exactly one `base` and one `join` target handle | `ConfigError` | `codegen._order_edge_join_incoming_edges` |
| Canonical submodel occurrence, definition, public handle, port id, or internal port endpoint is malformed | `ParseError` | `codegen.graph_to_code_multi` canonical preflight; no source is emitted. |
| Parent edge endpoint is neither a root node nor a registered occurrence (e.g. a definition-owned child id used as a parent endpoint) | `ParseError` naming the edge, endpoint side, and node id | `codegen.graph_to_code_multi` canonical preflight; no source is emitted. |
| `graph_to_code` called on a graph that actually produces >1 file | `ConfigError` | `codegen.graph_to_code` |
| Any emitted file fails `ast.parse` | `ConfigError` | `codegen._assert_emitted_files_parse` |
| `polars` transform has no code (any source count) | No error — emits a `NotImplementedError`-raising placeholder so the graph still saves; fails at run time, warned at save time | `_codegen_builders._gen_transform`, `_save_pipeline._validate_transforms_are_runnable` |
| `polars` transform with executable code and an input named `df` | `ConfigError` (node id/label) | `_codegen_builders._gen_transform` |
| Data Input, External File, Rating Step, Model Score, Scenario Expander or Explore node with an input named `df` | `ConfigError` (node id/label) | `_codegen_builders._reject_df_input` |
| stepped `polars` transform whose steps cannot be rendered | No error — incomplete placeholder body, warned at save time with the step index | `_codegen_builders._gen_transform`, `_save_pipeline._validate_transforms_are_runnable` |
| stepped `dataInput`, `externalFile`, `ratingStep`, `modelScore`, `scenarioExpander` or `explore` whose steps cannot be rendered | No error — the hook's body is the `INCOMPLETE_STEPS_MESSAGE` placeholder, warned at save time with the step index | `_codegen_builders._stepped_body_code` and `_configured_node`, `_save_pipeline._validate_transforms_are_runnable` |
| stepped `polars` original carrying `inputMapping` | `ConfigError` (node id/label) | `_codegen_builders._gen_transform` |
| `edgeJoin` codegen called with `!= 2` sources | `ConfigError` | `_codegen_builders._gen_edge_join` |
| `Explore` node with `!= 1` incoming edge | `ParseError` | `_codegen_builders._gen_explore` |
| Codegen dispatched on a `SUBMODEL`/`SUBMODEL_PORT` occurrence | `RuntimeError` | `_codegen_builders._gen_submodel_placeholder_unreachable` |
| Extraction engine given an unknown `kind` | `KeyError` | `_code_extraction.extract_user_code` |
| User code text fails to parse during extraction | `_UserCodeParseError` (`ParseError` + `ValueError`, chains original `SyntaxError`) | `_code_extraction._parse_user_code` |
| `_rewrite_outer_returns_as_assignment` hits a `return` fragment matching neither `return <expr>` nor bare `return` | `AssertionError` (`# pragma: no cover`, defensive) | `_code_extraction._rewrite_outer_returns_as_assignment` |

All of the `ParseError`/`ConfigError`/`HauteError` types are Haute's
canonical error hierarchy (`haute.errors`); the save-pipeline HTTP route
maps them to a 400 response and rolls back rather than leaving a partial
file tree on disk.

## Testing

- `tests/test_codegen_input_identity.py` — graph-to-source tests pin edge-derived input names as generated Python parameters and persisted `connect` metadata.
- `tests/test_codegen_layout.py` — the generated-file shape: a corpus graph covering every
  node type as a declaration and every code-accepting type as a hook (plus a transform, an
  instance and a submodel definition file) generates modules that `ruff format --check`
  (ruff's defaults) leaves unchanged and that `ruff check --isolated` passes with the
  pycodestyle, pyflakes, isort, pyupgrade, bugbear, comprehension, simplify, pie and
  empty-docstring rules (not flake8-return: a hook whose code ends by assigning `df` is
  followed by the generated `return df`, which RET504 reports, and the code is the user's);
  no module contains a loader call, a `haute._` import, a
  `__file__` expression or an empty docstring; `import polars as pl` appears only when the
  module refers to `pl`; a declaration has no annotations and its description is the whole
  body; long decorators, signatures and connect calls break as ruff breaks them; a string
  containing a double quote takes single quotes; each input-less source is emitted directly
  before its first consumer while other nodes keep their topological order; the transform
  output declaration appears only when the code does not make `df` local; and an input
  named `df` on a code-accepting node is a `ConfigError`.
- `tests/test_codegen_contract_keyword.py` — the contract keyword appears only when it adds
  information: omitted for an opaque contract and for one the node's settings already
  imply (a Data Output or Output), kept for a Model Score's model-derived inputs, a declared
  transform contract and `inputs_by_parent`; a file saved with the keyword omitted parses back
  to the same effective contract, and a second save is byte-identical.
- `tests/test_polars_steps.py::test_codegen_parse_round_trip_reproduces_rendered_code` — a stepped transform's generated module parses back to the rendered code and its steps; `test_stepped_original_rejects_input_mapping` covers the `inputMapping` rejection.
- `tests/test_polars_steps.py::test_data_input_steps_execute_and_round_trip`, `test_data_input_incomplete_steps_fail_on_every_path`, `test_data_input_hand_edit_discards_steps_without_sidecar_marker` and `test_data_input_free_code_with_redundant_parentheses_reloads_in_step_mode` — a stepped Data Input's generated module is a `df` hook whose body is the rendered lines and parses back to its steps, an unrenderable list emits the `INCOMPLETE_STEPS_MESSAGE` placeholder that reloads as empty code with the steps kept, a hand edit discards the steps without a `_discarded_sidecar` marker, and a rendering the source finaliser normalises still reloads in step mode.
- `tests/test_polars_steps.py::test_external_file_steps_reach_the_other_inputs_and_obj`, `test_external_file_unknown_input_and_input_mapping_fail_loudly`, `test_rating_step_steps_execute_and_round_trip`, `test_scenario_expander_steps_execute_and_round_trip` and `test_model_score_steps_round_trip_and_fail_before_any_model_loads` — each frame surface's generated module is a hook whose body is the rendering (after `df = <first input>` for an External File) and parses back to its steps, an unrenderable list emits the placeholder (an External File keeping its binding line) and reloads with the steps kept, and a stepped External File original refuses `inputMapping`.
- `tests/test_rename_stable_binding.py` — executes a coded transform before and after the rename shape the editor produces (edge renamed, `inputMapping` recording the logical name) with equal rows, shows the unmapped shape failing on the old name, and round-trips the mapping through codegen and the parser.

Tests live under `tests/`, organised roughly one file per concern rather
than one file per module:

- **`test_codegen.py`** — the largest suite; broad coverage of
  `graph_to_code`/`graph_to_code_multi` across every node type, param
  building, path-literal safety, live-switch/selected-columns/
  passthrough-vs-behavioural codegen, instance-node mapping (including
  ambiguous/missing-target error cases), submodel pipeline replacement,
  special-character labels, connect-call deduplication, contract-source
  collision handling, and an explicit single-file guard for `graph_to_code`.
  The input-identity scenarios live here: frame-labelled parameters for
  multi- and sole-frame `apiInput` edges (signature names equal the frame
  labels, in edge order), the explicit `source_port` on every `apiInput`
  connect call including sole-frame, the duplicate-input-name `ParseError`
  (frame vs frame is unreachable by schema validation; frame vs
  sanitised-node-label is the reachable case), and the round-trip fixpoint
  (`test_codegen_roundtrip_property.py`) regenerated under frame-named
  parameters.
- **`test_save_incomplete_transform.py`** — a transform the user has not written
  yet (no code, any upstream count) must SAVE with a warning
  rather than block the whole pipeline. Pins that the generated body fails
  loudly if run instead of silently passing one input through, and that it
  round-trips back to an empty node rather than being adopted as user code.
  Executor-level
  cases pin the same failure in the live canvas, and a
  submodel case proves save warnings cover embedded definitions.
- **`test_codegen_builders.py`** — per-builder unit tests (`_gen_api_input`,
  `_gen_banding`, `_gen_scenario_expander`, `_gen_optimiser`, `_gen_explore`,
  `_gen_data_input`, `_gen_data_output`): each returns a declaration without code and a
  hook with code, and `TestCodegenExecValidation` executes generated code to check that it
  is runnable, not just syntactically valid.
- **`test_codegen_injection.py`** — the triple-quote / brace / quote-inside-
  string bug class specifically: sanitize-description correctness,
  triple-quote injection attempts, curly braces and quotes in decorator values
  (printed by the literal printer), combined injection scenarios, and brace
  round-trip through the docstring.
- **`test_codegen_fail_loudly.py`** — the loud-failure contract directly:
  `inputs_by_parent` preservation and stale-key dropping, unparseable-file
  refusal (both single-file and submodel-file), a decorator value with no
  literal form, name-collision reporting across root+submodel, and the
  submodel-occurrence unreachability guard.
- **`test_codegen_docstring_roundtrip.py`** — "Phase 5 Wave 9D #122
  pathological docstring round-trip tests": adversarial description strings
  that must both compile and round-trip through `ast.get_docstring`
  bit-for-bit, including an end-to-end "torture" class.
- **`test_codegen_roundtrip_property.py`** — capstone property-based tests
  (uses Hypothesis — `test_hypothesis_roundtrip_semantics_and_source_bytes`)
  asserting codegen → parser → codegen is a fixpoint across a corpus of
  graphs covering every supported node type; also checks generated config
  sidecars are valid JSON and that edge-join reference remapping (vs.
  non-reference literals) is correct.
- **`test_codegen_execution_equivalence.py`** — an execution-differential
  harness: runs a saved standalone `.py` file and asserts it produces the
  same result as the in-process executor for the same batch, covering
  scenario-expander, optimiser-apply, live-switch, modelling, edge-join,
  constant, banding, rating-step and model-score declarations, a Data Input,
  Model Score and External File hook, plus `OUTPUT` — for the opposite reason:
  `OUTPUT` is *not* a passthrough, and this harness is what catches a
  standalone run silently reverting to one (its generated body once regressed
  to a bare `return {first}`, so a saved pipeline's `pipeline.run()` returned
  the raw upstream frame instead of the assembled response document).
- **`test_codegen_builders_contracts.py`** — small focused contracts not
  covered elsewhere: a live-switch declaration routing by the active scenario
  in a standalone run, and model-score registered-source settings staying in
  the sidecar.
- **`test_code_extraction_coverage.py`** — internal-helper unit coverage for
  `_code_extraction.py`: user-code parsing, bare-return rewriting, trailing-
  return stripping, declaration bodies (`...`, `pass`, docstring only), the
  External File binding matcher, finalisers, and the redundant-RHS-wrapper proof.
- **`test_code_extraction_roundtrip.py`** — "remediation 5.1 (C5) + 5.6":
  the chain-assignment unwrap proof, a chain-assignment save→load→save
  cycle, extraction failing loud on unparseable bodies, and External File
  code keeping the user's own leading imports.
- **`test_ast_return_boundaries.py`** — contract tests specifically for the
  outer-vs-nested return-boundary detection against a hand-written "line
  heuristic" reference, including textual-return misfires the old heuristic
  would have gotten wrong; separate classes for the hook and
  External File extractors.
- **`test_model_score_codegen.py`** — model-score-specific codegen and
  parser round-trip, including `_build_node_config` interaction.
- Several other files exercise codegen indirectly as part of broader
  round-trip / integration suites: `test_parser_roundtrip.py`,
  `test_multi_frame_end_to_end.py`, `test_commit6_port_aware_edges.py`,
  `test_submodel*.py`, `test_edge_join.py`, `test_preserve_markers.py`,
  `test_explore_round_trip.py`, `test_e2e.py`, `test_adversarial_inputs.py`,
  `test_save_pipeline_integrity.py`.

**Strategy mix:** predominantly unit and integration tests exercising the
public `graph_to_code`/`graph_to_code_multi` surface and individual
builders/extractors directly, layered with property-based testing
(Hypothesis, in `test_codegen_roundtrip_property.py`) for the round-trip
fixpoint invariant across a generated corpus, plus a differential-execution
harness (`test_codegen_execution_equivalence.py`) that compares saved-file
behaviour against the live executor rather than just checking source text.

**Known coverage approach worth noting:** the docstring/injection tests
(`test_codegen_injection.py`, `test_codegen_docstring_roundtrip.py`) are
explicitly framed around specific historical bug classes ("bug B2", "#122")
rather than a generic fuzz sweep — regressions in that area are pinned down
individually as they're found, consistent with the repo's TDD convention of
writing a failing test before the fix.

> Known gap: no test imports and runs a hierarchical `graph_to_code_multi()` main file through
> the live `Pipeline.run()` API. That API only records `pipeline.submodel(...)` paths, so runtime
> equivalence is intentionally established after static parse/flatten, not through live module
> registration.
## Recovery single-node generation

Single-node generation for a recovery candidate preserves an explicit authored
column contract but does not derive a new contract from external model artifacts.
This explicit offline mode emits the current declaration or hook without contacting external
services.
