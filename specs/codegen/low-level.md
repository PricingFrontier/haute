# Codegen — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/codegen.py` | Public orchestration API (`graph_to_code`, `graph_to_code_multi`); single-node dispatch (`_node_to_code`, `_generate_node_code`); instance-node handling; contract kwarg formatting/injection (`_format_contract_kwarg`, `_format_contract_source`, `_inject_contract_kwarg`, `_matching_close_paren`); pipeline/submodel file assembly (`_generate_pipeline_lines`); the final parse gate (`_assert_emitted_files_parse`). |
| `src/haute/_codegen_builders.py` | One `_gen_*` builder per `NodeType`, registered into `haute._registry.NODE_REGISTRY` via `@_register_codegen`. String-safety helpers (`_safe_str`, `_safe_path`), shared field extraction (`_common_node_fields`, `_build_params` — parameters are the per-edge input names supplied by the orchestrator, with a loud duplicate-name guard replacing the former name-suffixing behaviour), docstring sanitization (`_sanitize_description`), and per-type templates such as `_MODEL_SCORE`, `_BANDING_SINGLE`, and `_RETAINED_EXTERNAL`. |
| `src/haute/_registry.py` | Cross-component dependency owned by [pipeline-config](../pipeline-config/low-level.md): codegen registers and reads per-node code builders through the canonical registry. |
| `src/haute/_code_extraction.py` | Reverse direction of the codegen builders' body wrapping: strips generated boilerplate back out of a persisted function body so the user-facing code editor shows only what the user actually typed. Consolidated engine (`extract_user_code`) dispatches through `BOILERPLATE_MATCHERS`/`_FINALISERS` registries keyed by node "kind." |
| `src/haute/_ast_helpers.py` | Stateless AST/source utilities with no node/graph knowledge: literal evaluation (`_eval_ast_literal`), decorator introspection (`_get_decorator_kwargs`, `_is_pipeline_node_decorator`, `_get_decorator_node_type`), docstring/whitespace handling (`_strip_docstring`, `_dedent`), and whole-file extraction helpers (`_extract_function_bodies`, `_extract_connect_calls`, `_extract_meta`, `_extract_preamble`, `_extract_preserved_blocks`) shared with the parser. |

## Key types and data structures

- **`_NodeCodeFn`** (`codegen.py`) — `Callable[[GraphNode, list[str] | None, list[str] | None, list[str] | None], str]`; the injected per-node code generator (`_node_to_code` for pipelines, `_submodel_node_to_code` for submodel files), parameterizing `_generate_pipeline_lines` so both file kinds share one assembly routine. The fourth argument carries the per-edge source *function* names, kept alongside the edge-derived input names so edge-join role kwargs (`base_input`/`join_input`) can reference the connected source functions for parser reconstruction while the signature uses the input names.
- **`_ConnectPair`** (`codegen.py`) — `tuple[str, str, str | None, str | None]`: `(src_func, tgt_func, source_port, target_port)`. `source_port`/`target_port` are `None` for the bare `connect("a", "b")` form used by ordinary single-output sources. Every `apiInput` edge — including one from a sole-frame source — carries its frame label as `source_port`, so the generated file always names the frame each connection delivers; a bare connect from an `apiInput` is not emitted.
- **`CodegenBuilder`** (`_codegen_builders.py`) — `Callable[[GraphNode, list[str]], str]`; the signature every `_gen_*` function implements. Registered per `NodeType` into `NODE_REGISTRY[node_type].codegen` (see `haute._registry`); `NODE_REGISTRY` pairs each type's codegen builder with its exec-side runtime builder from `haute._builders`, and `validate_registry_complete` enforces both are present for every type.
- **`MatcherResult`** (`_code_extraction.py`) — `NamedTuple(start_idx: int, return_vars: tuple[str, ...], generated_scaffold: bool = False)`. Output of a `BoilerplateMatcher`: `start_idx` is the first line of `cleaned_lines` considered user code; `return_vars` are variable names whose trailing `return <var>` should be stripped; `generated_scaffold=True` means a generated `df = <helper>(...)` line already produced `df`, so the polars finaliser must not treat the node's first parameter as a strippable alias.
- **`BoilerplateMatcher`** (`_code_extraction.py`) — `Callable[[list[str], tuple[str, ...]], MatcherResult]`. One matcher per internal kind (`polars`, `source`, `scenario_expander`, `model_score`, `rating_step`, `external`), registered in `BOILERPLATE_MATCHERS`.
- **`_FINALISERS`** (`_code_extraction.py`) — `dict[str, Callable[[str, tuple[str, ...]], str]]`, the post-processing step per kind that runs after the shared strip-docstring → dedent → skip-boilerplate → strip-trailing-return pass (e.g. `_finalise_polars` unwraps redundant `df = (...)` parens and rewrites bare `return expr` to `df = expr`).
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
   `_error_on_name_collisions` over root nodes plus each referenced
   definition graph exactly once.4. **No-submodel path:** order edges (`_order_edge_join_incoming_edges` puts
   each edge-join's two incoming edges in base-then-join order), topo-sort
   nodes (`_topo_sort` via `haute._topo.topo_sort_ids`), build
   id→func-name maps and each node's per-edge input-name list
   (`edge_input_name(edge, source_node)` in edge order — the same list the
   executor derives, so signature and binding can never disagree), raising
   `ParseError` on a duplicate input name within one node (via the shared
   `duplicate_input_names` detector in `_graph_utils.py`), build
   `connect_pairs` directly from edges, call
   `_generate_pipeline_lines(kind="pipeline", ...)`, then
   `_assert_emitted_files_parse` on the single resulting file.
5. **Submodel path:** resolve every canonical occurrence and order definitions
   by first occurrence. For each definition, topo-sort its internal graph,
   derive child parameters from structured public input targets followed by
   internal edges, validate every structured output source, and emit one
   `haute.Submodel(..., definition_id=..., input_ports=...,
   output_ports=...)` file. Then omit occurrence nodes from the root function
   list, translate parent boundary handles only to declared public port ids,
   derive downstream names as `<alias>__<portId>`, and emit one explicit
   `pipeline.submodel(...)` registration per occurrence with its definition
   id, instance id, alias, and label. Parent `connect` calls refer to aliases
   plus public port ids; synthetic `in__`/`out__` handles never enter source.
   Finally `_assert_emitted_files_parse` validates every emitted file.

### `_generate_pipeline_lines` (shared by both paths above)

Builds the file as a list of lines: docstring header (name run through
`_sanitize_description` since it lands between the module docstring's triple
quotes) → standard imports → optional per-file preamble →
`Pipeline(...)`/`Submodel(...)` construction → any preserved blocks
(`_emit_preserved_blocks`, wrapped in `# haute:preserve-start/-end` markers)
→ original nodes (via `node_to_code_fn`) → instance nodes (via
`_instance_to_code`, decorator prefix rewritten to `@submodel.` inside
submodel files) → submodel import lines → `pipeline.connect(...)`/
`submodel.connect(...)` calls (JSON-encoded frame names via `json.dumps` so
labels containing quotes/backslashes/non-ASCII survive; deduped when
`dedup_connects=True`).

Preserved-block extraction is intentionally structural rather than byte-for-byte. The shared
`haute._ast_helpers._extract_preserved_blocks` scan claims only completed column-zero marker
pairs, removes marker lines and leading/trailing blank lines inside each block, ignores an
unmatched start, and returns blocks in source order. `_extract_preamble` excludes those completed
module spans, so the two stores are disjoint. Indented marker text remains in its enclosing
function or construct and is not separately extracted. `_generate_pipeline_lines` emits each
pipeline or submodel block once after object construction and before node functions, restoring
fresh markers.

### `_node_to_code` (per-node dispatch)

1. `_role_order_node_sources` — for `EDGE_JOIN` nodes only, reorders
   `source_names`/`source_ids` into base-then-join using
   `resolve_edge_join_role_indices` (from `haute._edge_join`); a no-op for
   every other node type.
2. `_generate_node_code` — looks up `NODE_REGISTRY[node.data.nodeType].codegen`
   and calls it; raises `KeyError` if either the entry or its codegen builder
   is missing.
3. If `has_config_folder(node_type)` (from `haute._config_io`), locate the
   first `\ndef ` in the generated code and replace everything before it
   with `@pipeline.<decorator>(config=<path>)`. The decorator lookup uses the
   complete `NODE_TYPE_TO_DECORATOR` mapping; a missing mapping or missing
   generated `def` raises `HauteError` with node context rather than silently
   defaulting or skipping the rewrite.
4. `_format_contract_kwarg` computes the `contract=...` kwarg text (or
   `None` for instance nodes whose contract comes from the referenced
   original node); if present, `_inject_contract_kwarg` splices it into the
   decorator, with any `HauteError` enriched with `node_id`/`node_label`/
   `node_type` before re-raising.

### Canonical data I/O and retained sidecar builders

- `_generate_pipeline_lines` emits the reserved `_HautePath` import before
  the standard imports and the `_HAUTE_CONFIG_BASE` assignment immediately
  after the `Pipeline`/`Submodel` constructor. A pipeline file assigns
  `_HautePath(__file__).resolve().parent`; a submodel file assigns
  `_HautePath(__file__).resolve().parents[N]` where `N` is the number of path
  separators in the recorded registration path — config paths always resolve
  against the parent pipeline directory, so the emitted base climbs exactly as
  many levels as the registration path descends (`modules/x.py` -> `parents[1]`,
  `x.py` -> `parents[0]`, `a/b/x.py` -> `parents[2]`). Submodel codegen without
  that depth is a `HauteError`. `_extract_preamble` excludes exactly those
  reserved scaffold shapes (`.parent` and `.parents[N]`, matched structurally,
  plus the `_HautePath` import) so codegen -> parse -> codegen emits one
  config-base assignment and reaches a source-text fixpoint rather than
  reclassifying generated infrastructure as authored preamble.
- `_gen_data_input` emits the one retained tabular-input scaffold. It calls
  `resolve_data_input_from_config` using the generated sidecar path and file
  directory, assigns the returned lazy frame to `df`, appends optional user
  code, and returns `df`. The `data_input` extraction matcher removes only
  its canonical imports, config-base setup, and load call on the reverse
  parse. Only the optional transform remains in node `code`.
- `_gen_data_output` emits a config-sidecar decorator and an ordinary
  pass-through body. It never writes during import or ordinary pipeline
  execution; explicit publication belongs to the output-write runtime path.
- `_gen_api_input` and `_gen_external_file` obtain their sidecar paths from
  `config_path_for_node` and delegate to
  `resolve_api_input_from_config` / `load_external_object_from_config` with
  `Path(__file__).resolve().parent`. Their source contains no baked copy of
  the sidecar's current data path, schema, file type, or model class.
  External-file user code follows the generated object load.
- Removed `dataSource`/`dataSink` enum values, decorators, templates, and
  extractor aliases have no compatibility path. Round trips preserve the
  retained I/O provider branch, format/mode, arguments,
  destination fields, connections, and user code without inventing inactive
  fields. No cache-mode field exists to round-trip; execution mode is derived.

### `_inject_contract_kwarg` / `_matching_close_paren`

Operates on the ALREADY-GENERATED source text (not an AST), because the
per-type builders never know about contracts. Splits on `"\n"` (not
`str.splitlines()`, since form-feed/NEL/LINE-SEPARATOR can legally appear
inside an emitted string literal but Python only breaks source lines at real
newlines — using `splitlines()` would desync from `tokenize`'s row
numbering). Finds the first `@pipeline.`/`@submodel.` line; if it has no
`(`, rewrites it to `name(contract=...)`; otherwise locates the matching
close paren with `_matching_close_paren` (a `tokenize.generate_tokens` walk
tracking paren depth from the first `(` at the known position — string- and
comment-aware, and lazy so malformed *body* code after the decorator can't
make it fail) and inserts `, contract=...` or `contract=...` depending on
whether the decorator already has args (determined by checking whether the
text between the parens is non-whitespace).

### `extract_user_code` (the extraction engine)

1. Trim leading/trailing blank lines from `body_source`, then
   `_strip_docstring` (AST-based: wraps the body in a synthetic `def _f():`
   so line numbers are recoverable, parses, and slices past
   `ast.get_docstring`'s end line — never a textual triple-quote scan,
   because escaped quotes inside the docstring content defeat that).
2. Look up the `kind`'s matcher and finaliser (`KeyError` if `kind` is
   unknown); run the matcher against the cleaned lines to get a
   `MatcherResult`.
3. Slice from `result.start_idx`, `_dedent`, strip a trailing
   `return <var>` for each `return_vars` entry via `_strip_trailing_return`
   (AST-based — `_strip_outer_trailing_return` only removes the return if it
   is the literal last OUTER-scope statement).
4. If `result.generated_scaffold`, finalise unconditionally through
   `_finalise_polars` with no param names (the scaffold already bound `df`);
   otherwise run the kind's registered finaliser with the real param names.

## Edge cases and invariants

- **Multi-edge into one node** (the same upstream `apiInput` feeding a node
  through two frame edges) — each edge contributes its own frame label as the
  parameter name, so the parameters are distinct by the api-input schema's
  label-uniqueness rule; no suffixing exists. A derived duplicate across
  *different* sources (frame label colliding with another input's name) is a
  `ParseError`, never a rename. Zero sources still yields the single default
  parameter name `"df"`.
- **User-controlled text inside decorator arg lists** (a column literally
  named `"price (gbp)"`, or containing `":)"`) — `_matching_close_paren`
  tokenizes rather than character-scans, so parens inside string literals
  and comments are invisible to the depth counter.
- **Descriptions containing triple quotes, backslashes, or edge whitespace**
  — `_sanitize_description` doubles every backslash, escapes every `"`
  (preventing any run of 3+ quotes from closing the enclosing `"""` early),
  and prepends a `\n` when the description has newlines or leading/trailing
  whitespace (neutralising `inspect.cleandoc`'s indent-stripping behaviour
  so a round-trip through `ast.get_docstring` reproduces the original
  bit-for-bit). Curly braces are deliberately left untouched because the
  sanitized value is always a `str.format` keyword argument, never spliced
  into template text — `str.format` does not re-scan substituted values.
- **`Contract.inputs_by_parent` stale keys** — a parent id present in the
  contract metadata but no longer connected after a UI rewire is *omitted*,
  not guessed at, logged via `contract_inputs_by_parent_omitted_stale`;
  edges/node bodies remain the source of truth for what's actually
  connected.
- **Two `inputs_by_parent` keys collapsing to the same emitted parent name
  with different column sets** — genuine ambiguity, raises `ParseError`
  rather than picking a "last writer."
- **`polars` transform with no code and no upstream sources, or no code with
  multiple upstream sources** — neither can RUN (nothing to return; no
  well-defined implicit multi-source passthrough), but both are ordinary states
  for a graph still being built, so neither blocks a SAVE. `_gen_transform`
  emits `_code_extraction.INCOMPLETE_TRANSFORM_BODY`: a valid body that raises
  `NotImplementedError` if executed. It never silently passes one input through
  and drops the rest, and never emits `return df` where `df` is unbound. Save
  reports it through `_validate_transforms_are_runnable` as a non-blocking
  warning, alongside the empty-`tables[]` API Input warning.
  The placeholder's message is a CONSTANT naming no node or source, so
  `_match_polars` can recognise it and treat it as scaffold — the node
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
  placeholder is preserved (`generated_scaffold=True`): the placeholder binds no
  `df` alias, so a following `df = <param>` is authored code, not scaffold.
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
  itself re-validates `len(source_names) == 2` and that both `baseInput`/
  `joinInput` are present in config, even though `_role_order_node_sources`
  should have already guaranteed this; documented as defensive against
  direct callers, not reachable via `graph_to_code`.
- **Cross-boundary edge-join role resolution at a submodel boundary** —
  `build_edge_join_boundary_target_roles` (from `haute._edge_join`)
  resolves which `target_port` an edge crossing INTO a submodel-hosted
  edge-join should carry, since the join's base/join role isn't visible
  from the root-graph edge alone.
- **Windows-style paths in generated `path=` literals** — `_safe_path`
  normalizes backslashes to forward slashes before escaping, so a pipeline
  saved on Windows and read on Linux (or vice versa) still parses
  correctly. Retained API Input and External File helpers additionally use
  the generated file's resolved parent as their base-directory candidate;
  other generated helpers retain their existing `Path(__file__).parent`
  spelling.
- **External-file user imports directly after the generated load** —
  `_match_external` is position-aware: imports BEFORE the generated
  `load_external_object_from_config(...)` call are stripped as
  boilerplate, imports AFTER it (or all imports, if there was no load at
  all) are preserved as user code.
- **One current generated scaffold per node type.** Extraction recognises
  the current scaffold plus ordinary user code; it does not carry aliases for
  retired generated chains or variable names. Rating-step codegen emits only
  canonical table fields and `combined_outputs`, never retired table labels
  or singular combined-output arguments.
- **Model-score / rating-step boilerplate call detection** —
  `_outer_boilerplate_call_end_line` locates `score_from_config(...)` /
  `apply_rating_step_from_config(...)` via an AST walk over a
  synthetically-wrapped body (so a top-level `return` stays valid), not a
  substring search — a token matching the call name inside a string literal
  or comment cannot mis-anchor the boilerplate boundary.
- **Hierarchical main files are a static-parser artifact, not a live import mechanism** —
  `pipeline.submodel(path)` only appends the path to the live `Pipeline` object's
  `_submodel_files`; it does not import child decorators. `_assert_emitted_files_parse` proves the
  file tree is syntactically valid, while parser round-trip tests prove the static path. Direct
  execution equivalence is covered only for flat/single-file generated graphs.

## Error handling

| Condition | Exception | Raised from |
|---|---|---|
| No codegen builder registered for a `NodeType` | `KeyError` | `codegen._generate_node_code` |
| Config-backed node has no decorator mapping or its builder emitted no function definition | `HauteError` with node id/label/type | `codegen._node_to_code` |
| Decorator arg list untokenizable / no matching close paren / no decorator found | `HauteError` (context enriched with node id/label/type) | `codegen._matching_close_paren`, `codegen._inject_contract_kwarg`, re-raised in `codegen._node_to_code` |
| Contract computation hits `ConfigError` | `ConfigError` (propagated) | `codegen._format_contract_kwarg` |
| Contract computation hits a non-infra exception (`TypeError`, `KeyError`, `HauteError` incl. `ContractMismatchError`) | propagated unchanged | `codegen._format_contract_kwarg` |
| `inputs_by_parent` ambiguous key collision | `ParseError` | `codegen._format_contract_source` |
| Duplicate sanitized function names across root graph + submodels, including exact duplicate labels | `ParseError` (all colliding buckets listed) | `codegen._error_on_name_collisions` |
| Duplicate derived input names among one node's incoming edges | `ParseError` (target node + colliding input name) | `codegen.graph_to_code_multi` (per-edge input-name assembly) |
| An `apiInput` edge carrying no `source_port`/`sourceHandle` (only reachable via a hand-edited file — the editor cannot create one) | `ParseError` naming the edge and source node | `codegen.graph_to_code_multi` (per-edge input-name assembly) |
| `edgeJoin` codegen source names/ids desynced | `ParseError` | `codegen._role_order_node_sources` |
| Canonical submodel occurrence, definition, public handle, port id, or internal port endpoint is malformed | `ParseError` | `codegen.graph_to_code_multi` canonical preflight; no source is emitted. |
| Parent edge endpoint is neither a root node nor a registered occurrence (e.g. a definition-owned child id used as a parent endpoint) | `ParseError` naming the edge, endpoint side, and node id | `codegen.graph_to_code_multi` canonical preflight; no source is emitted. |
| `graph_to_code` called on a graph that actually produces >1 file | `ConfigError` | `codegen.graph_to_code` |
| Any emitted file fails `ast.parse` | `ConfigError` | `codegen._assert_emitted_files_parse` |
| `polars` transform has no code and no/multiple sources | No error — emits a `NotImplementedError`-raising placeholder so the graph still saves; fails at run time, warned at save time | `_codegen_builders._gen_transform`, `_save_pipeline._validate_transforms_are_runnable` |
| `edgeJoin` codegen called with `!= 2` sources, or missing `baseInput`/`joinInput` | `ConfigError` | `_codegen_builders._gen_edge_join` |
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
  yet (no code, and either no upstream or several) must SAVE with a warning
  rather than block the whole pipeline. Pins that the generated body fails
  loudly if run instead of silently passing one input through, that it
  round-trips back to an empty node rather than being adopted as user code, and
  that the well-defined single-upstream passthrough is untouched.
- **`test_codegen_builders.py`** — per-builder unit tests (`_gen_api_input`,
  `_gen_banding`, `_gen_scenario_expander`, `_gen_optimiser`, `_gen_explore`,
  `_gen_data_input`, `_gen_data_output`) plus `TestCodegenExecValidation`, which executes
  generated code to check that it is runnable, not just syntactically valid.
- **`test_codegen_injection.py`** — the triple-quote / brace / paren-inside-
  string decorator-injection bug class specifically: sanitize-description
  correctness, triple-quote injection attempts, curly braces in values,
  combined injection scenarios, and brace round-trip through the docstring.
- **`test_codegen_fail_loudly.py`** — the loud-failure contract directly:
  multiline/bare decorator contract injection, `inputs_by_parent`
  preservation and stale-key dropping, unparseable-file refusal (both
  single-file and submodel-file), missing-decorator rejection, name-
  collision reporting across root+submodel, and the submodel-occurrence
  unreachability guard.
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
  scenario-expander, optimiser-apply, live-switch, and modelling nodes
  (the "genuine passthrough" node types called out in the high-level
  Design rationale), plus `OUTPUT` — for the opposite reason: `OUTPUT` is
  *not* a passthrough, and this harness is what catches a standalone run
  silently reverting to one (its generated body once regressed to a bare
  `return {first}`, so a saved pipeline's `pipeline.run()` returned the raw
  upstream frame instead of the assembled response document).
- **`test_codegen_builders_contracts.py`** — small focused contracts not
  covered elsewhere: live-switch body scenario-awareness, and model-score
  registered-source decorator kwargs.
- **`test_code_extraction_coverage.py`** — internal-helper unit coverage for
  `_code_extraction.py`: user-code parsing, bare-return rewriting, trailing-
  return stripping, df-alias detection, matcher edge cases, identifier
  rewriting, finalisers, and the redundant-RHS-wrapper proof.
- **`test_code_extraction_roundtrip.py`** — "remediation 5.1 (C5) + 5.6":
  the chain-assignment unwrap proof, a chain-assignment save→load→save
  cycle, extraction failing loud on unparseable bodies, and external-file
  import preservation (the position-aware boilerplate boundary).
- **`test_ast_return_boundaries.py`** — contract tests specifically for the
  outer-vs-nested return-boundary detection against a hand-written "line
  heuristic" reference, including textual-return misfires the old heuristic
  would have gotten wrong; separate classes for the model-score and
  external-file extractors.
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
