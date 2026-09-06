# Pipeline Config — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/pipeline.py` | `Node` / `NodeRegistry` / `Pipeline` / `Submodel`: the decorator API, `connect()`, the standalone `run()`/`score()` executor, `to_graph()` (live-object → React-Flow dict). |
| `src/haute/_config_builder.py` | Per-node-type config dict construction from decorator kwargs + function body (`_build_node_config`); sidecar resolution and the parse-time `contract=` cross-check (`_resolve_node_config`). For Live Switch nodes, `config["inputs"]` records only positional edge parameters (frame labels for apiInput edges, sanitised source labels otherwise), the same strings referenced by the input-to-scenario mapping; keyword-only configuration parameters are excluded. |
| `src/haute/_config_io.py` | Sidecar JSON path conventions (`NODE_TYPE_TO_FOLDER`), read/write helpers, `collect_node_configs` (graph → sidecar files), per-type validation/normalisation of canonical configs, and the Windows-reserved-filename guard. |
| `src/haute/_config_validation.py` | `VALID_KEYS` registry derived from each node type's TypedDict definition, and `warn_unrecognized_config_keys`. |
| `src/haute/_builders.py` | Cross-component dependency owned by [execution-engine](../execution-engine/low-level.md): pipeline configuration consumes its `NODE_REGISTRY` registration contracts. |
| `src/haute/_node_builder.py` | Cross-component dependency owned by [execution-engine](../execution-engine/low-level.md): pipeline configuration documents its builder-interception seam. |
| `src/haute/_contracts.py` | Pipeline-config-owned `Contract`/`ColumnContract` model and registry-backed `get_column_contract()` lookup used by parse-time validation and execution. |
| `src/haute/_registry.py` | Pipeline-config-owned `NODE_REGISTRY` storage shared with execution and codegen. |
| `src/haute/_graph_builders.py` | AST node-skeleton discovery separated from resolution; the strict builder resolves every skeleton fail-loud before producing canonical `GraphNode`/`GraphEdge`, while editor recovery resolves skeletons independently. |
| `src/haute/_parser_regex.py` | [expression-parsing](../expression-parsing/low-level.md)-owned neutral syntax-recovery fragments (metadata, decorated functions, declared connections, and submodel registrations). It does not sit behind either strict parser entry point and does not return a canonical graph. |
| `src/haute/_pipeline_recovery.py` | [server-api](../server-api/low-level.md)-owned editor-only recovery orchestration and availability diagnostics, forbidden to canonical parser consumers. |
| `src/haute/_graph_shape.py` | Topology-only invariants independent of any single node's config (`validate_graph_shape_contracts`, `validate_pipeline_graph_shape_contracts`), including submodel child graphs. |
| `src/haute/_scaffold.py` | `haute init` template strings: `haute.toml`, `.env.example`, CI YAML for 3 providers × 7 deploy targets, starter pipeline/tests/utilities, pre-commit hook. |
| `src/haute/_project.py` | Project-root discovery (`get_project_root`, `is_haute_project`) and pipeline-file resolution (`resolve_pipeline_file`, 4-tier fallback). |
| `haute.toml` (repo root) | Concrete instance of the schema emitted by `src/haute/_scaffold.py::haute_toml`; `[project].pipeline` is read back by `src/haute/_project.py::_toml_configured_pipeline`. |

## Key types and data structures

- **`Node`** (dataclass, `src/haute/pipeline.py`) — `name`, `description`, `fn`, `is_source`,
  `config: dict`. Derived properties: `is_deploy_input` (config `_node_type == API_INPUT` or
  `api_input=True`), `is_live_switch`, `n_inputs`, `input_arity` (an `_InputArity` computed by
  inspecting `fn`'s signature — keyword-only params are config, not edges; positional params
  with defaults are optional edges). `__call__` validates the number of wired DataFrame
  arguments against `input_arity` before invoking `fn`.
- **`_InputArity`** (frozen dataclass) — `min_inputs`, `max_inputs: int | None` (`None` means
  unbounded via `*args`). `accepts(received)` / `describe()`.
- **`RegisteredEdge`** (frozen dataclass) — `source`, `target`, `source_port`, `target_port`:
  the in-memory edge representation `NodeRegistry`/`Pipeline` operate on. Distinct from the
  Pydantic `GraphEdge` (`haute._types`) that `_graph_builders.py` produces for the parsed
  source graph.
- **`NodeRegistry`** — base class for `Pipeline` and `Submodel`; holds `_nodes: list[Node]`,
  `_node_map: dict[str, Node]`, `_edges: list[RegisteredEdge]`, `_submodel_files: list[str]`.
- **`Pipeline(NodeRegistry)`** — adds `run()`, `score()`, `to_graph()`, `submodel()`,
  `submodel_files`.
- **`Submodel(NodeRegistry)`** — no `run`/`score`/`to_graph`. A live
  `Pipeline.submodel(file, *, definition_id, instance_id, alias, instance_of=None)` call
  validates that `alias` is a canonical identifier (no `label=` parameter accepted), records
  the occurrence, and records the path in `_submodel_files`; it does not import
  the module or register the `Submodel` object's nodes onto the live `Pipeline`. Static parsing
  resolves those files into the hierarchical/flat graph used by the full executor.
- **`NodeBuildContext`** (frozen dataclass, slots — `src/haute/_builders.py`, owned by
  execution-engine) — the parameter bundle
  shared by every per-type exec builder: `node`, `source_names`, `source_ids`,
  `target_handles`, `row_limit`, `node_map`, `orig_source_names`, `preamble_ns`, `source`,
  `upstream_ids`, `required_output_columns`, `reuse_loaded_model`, `execution_profile`,
  `source_ports`. `func_name` and `config` are derived properties.
- **`ColumnContract`** (`src/haute/_contracts.py`) =
  `tuple[set[str] | None, set[str] | None]` — `(produced, referenced)`;
  `None` on either side means opaque for that side. `OPAQUE_CONTRACT = (None, None)` is the
  explicit "declared opaque" sentinel, distinct from "no contract registered at all".
- **`NODE_REGISTRY`** (`src/haute/_registry.py`, populated on the execution side via
  `haute._builders._register`) — the single
  source of truth mapping `NodeType → (exec builder callable, column_contract callback,
  is_behavioural flag)`. Both `_config_builder.py`'s parse-time contract check and (out of
  scope here) the executor/codegen read this registry; a `NodeType` with no exec entry is
  treated as a registration bug (`KeyError`), never silently skipped.
- **`SharedNodeSemantics` / `MODELLING_NODE_SEMANTICS`** (`src/haute/_registry.py`) —
  the closed first-connected-input passthrough policy and decorator config keys
  shared by the modelling runtime/codegen pair. Both builders consume this one
  declaration; no other node type inherits the policy without a cross-path
  result contract.
- **`VALID_KEYS`** (`_config_validation.py`) — `dict[NodeType, frozenset[str]]`, precomputed
  at import time from each node type's config `TypedDict.__annotations__` plus
  `_UNIVERSAL_KEYS` (`instanceOf`, `inputMapping`, `selected_columns`, `column_renames`,
  `categorical_levels`, `contract` — keys any node type may legitimately carry).
- **`NODE_TYPE_TO_FOLDER` / `FOLDER_TO_NODE_TYPE`** (`_config_io.py`) — the bidirectional
  map between a `NodeType` and its `config/<folder>/` sidecar directory name. 14 of the 19
  node types store external config (all except `polars`, `edgeJoin`, `explore`, `submodel`,
  and `submodelPort`):

  | Node type | Sidecar folder |
  |---|---|
  | `apiInput` | `config/quote_input/` |
  | `dataInput` | `config/data_input/` |
  | `dataOutput` | `config/data_output/` |
  | `liveSwitch` | `config/source_switch/` |
  | `modelScore` | `config/model_scoring/` |
  | `banding` | `config/banding/` |
  | `ratingStep` | `config/rating_step/` |
  | `output` | `config/quote_response/` |
  | `externalFile` | `config/load_file/` |
  | `modelling` | `config/model_training/` |
  | `optimiser` | `config/optimisation/` |
  | `optimiserApply` | `config/apply_optimisation/` |
  | `scenarioExpander` | `config/expander/` |
  | `constant` | `config/constant/` |
- **`TARGETS`** (`_scaffold.py`) — `dict[str, _TargetConfig]`, the 7-entry registry (one per
  supported `--target`) that every scaffold template dispatches through: `label` (for the
  `.env.example` header), `env_body` (literal credential block), `secrets` (ordered CI
  secret/env-var names), and `toml_section` (a `Callable[[str], str]` building that target's
  `[deploy.*]` TOML block from the project name). `_get_target` raises `ValueError` on an
  unknown target rather than returning a default.
- **`GraphNode` / `GraphEdge` / `NodeData` / `PipelineGraph`** (`haute._types`, not owned by
  this component but constructed here in `_graph_builders.py`).

## Control flow

There are two independent ways to arrive at "a graph", and they use different heuristics:

**1. Live Python object graph (`src/haute/pipeline.py`).** A `Pipeline` instance is built directly by
decorator calls at import time. `Pipeline.run()`/`Pipeline.score(df)` topologically sort the
in-memory `_edges`/`_nodes` via `haute.graph_utils.topo_sort_ids` (raising `CycleError` on a
cycle). With no edges, `_topo_order` returns registration order; it does not create a chain.
Execution then fails at `_execute_transform` if a non-source node has no inbound edge, rather
than inferring one from its parameter names. Otherwise it executes each `Node`'s `fn`, threading
DataFrames along declared edges — each edge's frame resolved port-aware through the shared
`_pick_source_frame` selection on `RegisteredEdge.source_port` — and resolves the return value
through `_resolve_output_node`: an explicit `@pipeline.output` node wins if there is exactly one;
otherwise the single node with no outgoing edge; otherwise raise, naming every candidate node.
`Pipeline.to_graph()` converts the same live objects into a React-Flow-shaped plain `dict`,
inferring each node's display type from `config["_node_type"]` if present, else `DATA_INPUT`
for an input node, else `OUTPUT` for the last-registered node, else `POLARS`. It delegates
node and edge construction to the same `_build_rf_nodes`/`_build_edges` path as static
parsing, so explicit connections and positional parameter-name inference are represented
consistently; keyword-only parameters remain configuration rather than edges.

**2. Strict static source graph (`_graph_builders.py` + `_config_builder.py`).** Given an
already-parsed AST module and pre-extracted function bodies, skeleton discovery records each
decorated function's identity, decorator token/kwargs, parameters, body, and source span
without loading external config. The strict `_extract_decorated_nodes` path then resolves
every skeleton and propagates any failure. For each skeleton it calls `_resolve_node_config`, which
either: loads and normalises a `config=` sidecar via `_config_io.load_node_config` and
attaches code parsed from the function body (`_attach_code_from_body`); raises
`_sidecar_required_error` if the node type is folder-backed but no `config=` was given; or
dispatches into `_build_node_config`'s per-`NodeType` branch to build the config purely from
decorator kwargs + body. `_resolve_node_config` also pops a `contract=` kwarg before
delegating (so per-type builders don't flag it as unrecognised), cross-checks it via
`_validate_user_contract`, and re-attaches it to the config afterwards. The resulting raw node
dicts feed `_build_edges` (explicit `connect()` tuples in one four-field
`(source, target, source_port, target_port)` form,
plus implicit parameter-name-matching edges; edges are never invented, so a file declaring no
wiring parses as a disconnected graph) and `_build_rf_nodes` (assigns x-spaced GUI positions) to
produce
the final `list[GraphNode]`/`list[GraphEdge]` — the graph the frontend, codegen, and the real
executor operate on.

`parse_pipeline_source()` converts `SyntaxError` into a contextual `ParseError` and never
calls regex recovery. `parse_pipeline_file()` inherits that strict behaviour. The separate
editor recovery service consumes the same AST skeletons when syntax is valid and neutral
`_parser_regex` fragments otherwise, catches expected authored failures per named node, and
constructs only recovery DTOs. No recovery value can be passed to `_build_rf_nodes`, codegen,
execution, lint, deploy, or strict post-save verification.

**Sidecar write path.** `_config_io.collect_node_configs(graph)` walks a `PipelineGraph`,
skips node types without a config folder, instance nodes (`config["instanceOf"]` set), and
nodes flagged `config["_load_error"]` (protects the on-disk file from a bad in-memory state
clobbering it), preserves exact incoming-edge names in Optimiser and Optimiser Apply config
without node-id remapping. Before allowlist filtering, known removed identity fields are rejected:
Edge Join's `baseInput`/`joinInput` and Optimiser's `scored_input`/`factors_input` never become a
silent write-time migration. Each accepted config is then filtered through
`_prepare_config_for_sidecar` (strips `code`/`_`-prefixed keys recursively via
`_strip_internal_keys`, applies the `VALID_KEYS` allowlist — logging any dropped keys at
WARNING — then per-type canonicalisation for `BANDING`/`RATING_STEP`), and serialises the result
to `{relative_path: json_string}`. Rating-step canonicalisation always emits ordered entry rows.
Any validation error is raised while collecting/staging content, before an existing sidecar is
replaced.

**Registry-driven contract lookup.** `haute._builders._register(node_type, columns=..., opaque=...,
is_behavioural=...)` decorates roughly twenty per-type builder functions, registering both the
exec builder and — mutually exclusive — either a column-contract callback or the `opaque=True`
sentinel into `NODE_REGISTRY`. `haute._contracts.get_column_contract(node_type, config)`, called from
`_config_builder._derive_parse_time_contract` during the parse-time contract cross-check,
looks up and invokes the registered callback; a `NodeType` with none registered raises
`KeyError` rather than silently falling back to opaque, so a new node type added without a
contract registration is caught immediately.

**Project/pipeline resolution (`_project.py`).** `resolve_pipeline_file(path)`: `None` →
`_resolve_default_in(cwd)`; an existing directory → the same fallback scoped to it; an
existing file → resolved as-is, no discovery; a non-existent path → `FileNotFoundError`.
`_resolve_default_in` tries, in order: `[project].pipeline` from `haute.toml` (existence and
"looks like a pipeline" both checked — either failure raises `FileNotFoundError` naming the
configured path, so a typo never silently falls through to auto-discovery); a root-level
`main.py`; the single root-level `.py` file containing the literal substring
`"haute.Pipeline"`; otherwise raise, enumerating zero or multiple candidates.
Malformed/unreadable TOML raises `ConfigError` before discovery. Deploy resolves the selected
project through this helper immediately before binding a pipeline. The plural GUI discovery API
shares the configured-path checks but additionally lists valid sibling pipelines.

**Scaffold generation (`_scaffold.py`, driven by `cli/_init_cmd.py::handle_init`).** Every
template function is parameterised by `target`/`ci` and looks up per-target facts through
`TARGETS`/`_get_target` rather than branching on the target string directly, so adding a
target means adding one registry entry, not touching every template function.
`starter_pipeline(name)` emits imports plus a named `haute.Pipeline` declaration and no decorated
nodes. `starter_test(name)` parses that file and asserts only the configured pipeline name.
`handle_init` creates empty config, data, model, and output placeholder subdirectories under
`rating/`, but no node sidecars or `prompts/` directory, and removes any root `main.py` before
writing `rating/main.py`.
`haute_toml()` assembles `[project]`/`[deploy]`/`[test_quotes]`/`[safety]`/`[safety.approval]`
(`min_approvers` hardcoded to 2 in the template — solo users lower it by hand)/`[ci]`/
`[ci.staging]`/`[server]` sections, splicing in `_target_section()`'s
`[deploy.<target>]` block. `[server].host` and the optional closed `[assistant]` plus
`[assistant.egress]` tables are part of the shared TOML schema consumed by
`DeployConfig.from_toml`, even though neither is a deploy setting. The assistant key sets are
owned by `haute.assistant._config` so deployment parsing and assistant readiness cannot drift.
`env_example()` and the three CI-YAML generators (`github_ci_yml`/`github_deploy_yml`/
`github_deploy_prod_yml`, `gitlab_ci_yml`, `azure_devops_yml`) all pull the same `secrets`
list out of `TARGETS` through provider-specific formatters (`_github_secrets_env`,
`_gitlab_secrets_env`, `_azure_devops_secrets_env`) so a target's credential list is defined
once and rendered three ways. `handle_init` calls exactly the generator(s) for the chosen
`ci` value (`"none"` calls none of them); on `--force` it first calls
`_prune_stale_ci_files(project_dir, keep=ci)`, which deletes every other provider's known
artifact paths (from the `_CI_ARTIFACTS` map) and removes the resulting empty
`.github/workflows/`/`.github` directories, before writing the new provider's files.
Every generator result must parse as one complete YAML document for every
`TARGETS` entry. Structural tests locate the actual validate, staging, smoke,
impact, and production jobs/stages, assert their branch/manual conditions, and
compare each consuming step's environment/variables mapping with the target's
registry-derived secret list. Header-only parsing and substring checks are not
accepted as evidence for this boundary.

`tests/test_docs_accuracy.py` builds a real Databricks/GitHub scaffold on
`tmp_path`, inventories its before/after tree, parses the generated starter
pipeline for its node count, and compares marker-delimited documentation facts
with those results. It also extracts documented `haute <command>` names and
checks them against the commands printed by root `haute --help`; Python import
statements in documentation examples are resolved with `importlib`. Pure
comparison helpers accept supplied text so negative tests can prove that a
drifted tree, stale node count, phantom command, or nonexistent public import
produces a violation.

**Live arity and switch dispatch (`src/haute/pipeline.py`,
`src/haute/_builders.py`).** `Node`
computes frozen `_InputArity` once from `inspect.signature(fn)` at
registration/construction. `POSITIONAL_ONLY` and
`POSITIONAL_OR_KEYWORD` parameters become edge inputs, defaults lower the
minimum, `KEYWORD_ONLY` parameters are ignored as configuration, and
`VAR_POSITIONAL` makes the maximum unbounded; other variadic shapes are
rejected. Live-switch dispatch distinguishes no mapping (default-source
fallback) from a present mapping (mandatory active-scenario lookup). A miss
raises `LiveSwitchScenarioError(ExecutionError)` with deterministic
`available_mappings`; the public contract adapter supplies the same stable
code and fields to synchronous 422 and background `contract_error` paths.

**Canonical Data I/O construction.** `NodeType`, `DataInputConfig`,
`DataOutputConfig`, and `DECORATOR_TO_NODE_TYPE` define the same 19-value
vocabulary. `NodeRegistry.data_input()`/`.data_output()` allow multiple
instances; `_resolve_output_node` still treats multiple terminal writers as
ambiguous without an explicit `OUTPUT`. `_config_io.py` assigns only the
`config/data_input/` and `config/data_output/` folders,
`_config_validation.py` enforces their discriminated branches, and
`_config_builder.py` extracts only Data Input's post-read Polars body into
`code`.

**Retained input resolution.** Generated `apiInput` and `externalFile`
decorators pass only their sidecar path and module directory to
`resolve_api_input_from_config` or `load_external_object_from_config`;
declarative fields are not interpolated into generated bodies. The helpers
also accept the executor's already-resolved inline mapping. Path inputs go
through `load_node_config` and shared project/pipeline resolution. API input
validates non-empty paths and JSON `tables[]` before reading/shredding and
forwards projection/profile fields; external-file resolution validates
`path`/`fileType` and forwards `modelClass`. Invalid tables raise
`ApiInputSchemaError`, which the HTTP contract adapter maps to 422.

## Edge cases and invariants

- `Pipeline.to_graph()` materialises live registrations through `_build_rf_nodes` and
  `_build_edges`; it does not maintain a second node-type or edge-inference implementation.
- A static `pipeline.connect()` endpoint must be a root node or a registered occurrence alias
  with a declared public port. A definition-owned child id, like any other unknown endpoint,
  raises `ParseError` from the parser's conservation gate with the authored connection in
  `dangling_edges`; the document loads as non-ready with that diagnostic and Save is refused
  until the source is repaired. The live `Pipeline.connect()` rejects the same mistake
  immediately because live submodel children are not registered there.
- A Polars node's positional parameters are exactly its connected inputs, by name: an
  ordinary source contributes its sanitised node name, an API input its frame handle, and an
  occurrence its own name (`a`, or `a__<port_name>` when the definition declares several output
  ports); a declared `inputMapping={logical: connected}` lets the code keep another name. The
  parser infers nothing else: a parameter that matches no connected input, a connected input
  with no parameter, or a duplicate raises `ParseError` (`unbound_parameters`,
  `unconsumed_inputs`, `connected_inputs`, `remediation`) from the binding gate; the document
  loads as non-ready with that diagnostic and Save is refused, so a Save can never rewrite the
  authored signature (F13).
- Duplicate node function names are rejected twice, independently: at live registration
  (`NodeRegistry._register_node`, `ValueError`) and at static parse time
  (`_extract_decorated_nodes`, `ParseError`) — because the function name becomes the graph
  node id and a silent collision would drop a node's config entirely.
- `async def` node bodies are rejected only in the static parse path. A live `Pipeline` can
  register an `async def`-defined function via a decorator with no error; the rejection only
  fires once that same source file is parsed by `_graph_builders`.
- `OUTPUT` and `DATA_INPUT`/`DATA_OUTPUT` are folder-backed types, so their branches inside
  `_build_node_config` are unreachable on the healthy path — `_resolve_node_config`'s sidecar-
  required check raises first. The branches are kept as explicit no-ops (not removed) so a
  stray inline decorator usage can't silently fall through to the generic "transform" branch
  and pick up a `code` config it shouldn't have.
- `warn_unrecognized_config_keys` treats an unrecognised `NodeType` string (e.g. from a
  hand-edited or forward-incompatible sidecar) as "nothing to validate against" and returns an
  empty list rather than raising.
- Windows-reserved device filenames (`CON`, `PRN`, `AUX`, `NUL`, `COM1`–`COM9`, `LPT1`–`LPT9`)
  are matched on the stem before the first dot, casefolded, with trailing dots/spaces
  stripped — and rejected on every OS, not gated behind a platform check, so a project saved
  on Linux/macOS stays loadable on a Windows checkout.
- `_toml_configured_pipeline` raises `ConfigError` for malformed/unreadable TOML, so
  `resolve_pipeline_file` cannot silently discard the configured tier and bind another file.
- Ambiguous auto-discovery (2+ root `.py` files matching, no `main.py`, no configured TOML
  pipeline) raises rather than picking one alphabetically — deliberate, per the module
  docstring's "never silently picks a random file" contract.
- `_looks_like_pipeline_file` is a literal substring check for `"haute.Pipeline"` in the file
  text, not an AST/import check, so the string appearing in a comment or docstring would
  false-positive; an `OSError` reading one candidate file is silently skipped rather than
  failing discovery for the whole directory.
- `_prepare_config_for_sidecar`'s `VALID_KEYS` allowlist step is skipped entirely for any
  `NodeType` absent from `VALID_KEYS` (e.g. `SUBMODEL_PORT`, which has no `TypedDict` to
  anchor an allowlist on) — such configs fall through to the pre-existing code/internal-key
  stripping only, with no key-level filtering.
- Config read/write accepts only each node type's current schema. It neither
  classifies nor upgrades earlier emitted fields; where a canonical normaliser
  exists, both paths call it directly rather than a migration-named wrapper.
- A polars node with a self-contained code body and no upstream wiring (`_build_transform` in
  `_builders.py`) is treated as a source (`is_source=True`) rather than requiring an input —
  the code is expected to construct its own frame (e.g. `pl.DataFrame(...)`).
- `_azure_devops_secrets_env` takes an explicit `indent` because the same secrets block is
  spliced into the generated YAML at two different nesting depths: job-level `env:` blocks
  sit at 12 spaces (so keys need 14), while the `DeployProduction` stage's `runOnce.deploy`
  strategy nests `env:` at 18 spaces (so keys need 20). `azure_devops_yml` calls the helper
  twice with different `indent` values for this reason — sharing one indent across both call
  sites would under-indent the production secrets block into unparseable YAML.
- **Azure DevOps approval boundary.** The workflow's `environment: production`
  block only names the environment; its approval check is configured in the
  Azure DevOps portal, not emitted by `_scaffold.py`. A freshly scaffolded
  project's production stage runs unapproved until an operator configures that
  check out of band.

## Error handling

- **`ConfigError`** (`haute.errors`) — missing/unreadable/invalid-JSON sidecar; sidecar
  content failing schema validation; a folder-backed node type used without `config=`;
  `optimiserApply` misconfiguration (`artifact_path` set without `sourceType`); `modelScore`
  misconfiguration (a non-string or unsupported `sourceType`, or a blank
  `run_id`/`registered_model` for the declared source); project
  root not found, or found without a surrounding git repository.
- **`ParseError`** (`haute.errors`) — `async def` node body; duplicate node function name;
  Explore-node topology violations (`_graph_shape.py`).
- **`ContractMismatchError`** (`haute.errors`) — a user-declared `contract=` disagrees with
  the config-derived contract on the inputs and/or outputs side; the message lists which
  columns are missing from, or extra in, the builder-derived side.
- **`CycleError`** (`haute._topo`) — a cycle in the live `Pipeline` graph, propagated from
  `topo_sort_ids` through `Pipeline._topo_order` with the participating node names.
- **`ExecutionError`** (`haute.errors`) — live node arity mismatch; multiple explicit output
  nodes; ambiguous terminal nodes; multiple or ambiguous `score()` seed sources; unresolved
  `@pipeline.instance` registrations in the standalone executor (identified by the decorator's
  internal marker even when `instanceOf`/`inputMapping` are empty); a bare-frame `score()`
  seed against a source with two or more distinct connected `source_port`s; a dict seed whose
  keys do not exactly match the distinct connected ports (missing and unknown port names are
  both listed in the message — a one-port source accepts only the exact one-key dict); and a
  dict seed of any content against a zero-port source (source-only pipelines take a bare
  frame).
- **`ValueError`** — empty live pipeline; an unwired non-source node or missing upstream result
  during `Pipeline.run()`/`score()`; unknown source or target node in `connect()`; empty-string
  port name; duplicate key in a sidecar JSON object
  (`reject_duplicate_keys_hook`); non-`dict` sidecar JSON content; a resolved config path
  escaping the `config/` directory (`config_path_for_node`); a node name containing path
  separators or `..`.
- **`FileNotFoundError`** — `resolve_pipeline_file`/`_resolve_default_in`: a configured-but-
  missing `[project].pipeline` path; a configured path that doesn't look like a pipeline file;
  ambiguous or absent auto-discovery, enumerating what was tried either way.
- **`KeyError`** — `get_column_contract` when a `NodeType` has no registered contract callback
  (a registration bug, not a runtime/user-facing condition); a `NODE_REGISTRY` lookup miss in
  the exec-builder dispatcher.
- **`TypeError`** — an invalid (non-`str`, non-`None`) `source_port`/`target_port` passed to
  `connect()`, raised from `_validate_port`.
- **Never raises** — `warn_unrecognized_config_keys` logs at WARNING and returns the offending
  key list instead; this is the one deliberate departure from the component's fail-loud
  default, applied both when a config is first built and again when it is written to its
  sidecar.

## Testing

- `tests/test_column_contracts_adoption.py` verifies builder contract adoption, parser/executor boundary enforcement, codegen metadata, model-score exceptions, and overhead benchmark.
- `tests/test_registry_contracts.py` verifies exec/codegen registration metadata, duplicate/missing-entry failures, readiness/idempotence, and behavioural-body detection.
- `tests/test_sidecar_golden.py` verifies canonical sidecar JSON emission and loader round-trip.

Tests live under `tests/`, predominantly as behavioural unit tests against the real decorator
API and real JSON round-trips rather than mocks:

- **`test_pipeline.py`** — `Node`/`Pipeline`/`Submodel` decorator registration,
  arity validation (`TestNodeArityValidation`), edge wiring and topo-order delegation, output
  resolution (`TestOutputResolution`), duplicate-name rejection
  (`TestDuplicateNodeName`), instance-reference fail-loud behaviour
  (`TestInstanceReferencesFailLoud`), the API-input deploy-seed marker
  (`TestApiInputDecoratorMarksSeed`), and `to_graph()` shape/inference (`TestPipelineEdgeCases`
  and scattered `to_graph` tests across other classes). The input-identity release adds the
  full `score()` seed matrix here: an unnamed (`None`) source edge is the whole-output
  channel and does not create a labelable port; bare frame accepted at zero named ports
  (including that unnamed-edge case) and one named port; bare frame rejected at 2+ named
  ports; exact one-key dict accepted at one named port; dict
  rejected with missing keys, with unknown extra keys, and against a zero-port source — every
  rejection an `ExecutionError` naming the ports — plus `run()` port-aware frame selection
  for one- and many-frame apiInput sources.
- **`test_config_io.py`** + **`test_config_io_gaps.py`** — sidecar save/load
  round-trips, path conventions (`TestConfigPathForNode`), Windows-reserved-filename rejection
  (`TestIsWindowsReservedFilename`), `collect_node_configs` (including load-error protection
  and id remapping), banding compaction, rating canonical-row validation/emission,
  and preservation of the prior sidecar when validation fails.
- **`test_config_validation.py`** — `VALID_KEYS` registry completeness
  (`TestValidKeysRegistry`), `warn_unrecognized_config_keys` behaviour, and alignment between
  each type's decorator kwargs and the config keys `_build_node_config` actually produces
  (`TestBuildNodeConfigProducesValidKeys`, `TestConfigKeyTupleAlignment`).
- **`test_parser_helpers.py`** — AST extraction (`TestExtractDecoratedNodes`),
  decorator kwarg parsing, `_build_node_config` per node type
  (`TestBuildNodeConfigExtended`), `_resolve_node_config` sidecar and contract paths
  (`TestResolveNodeConfig`), and edge/GraphNode building (`TestBuildEdges`,
  `TestBuildRfNodes`), including deferred cross-boundary endpoints and fail-loud rejection of
  genuinely dangling connects.
- **`test_graph_shape_contracts.py`** — Explore in/out-degree contracts
  (`TestExploreGraphShape`), single-node and empty-graph edge cases, submodel boundary handle
  matching, and round-trip drift (`TestRoundTripDrift`).
- **`test_scaffold.py`** — every CI provider × deploy target combination, complete
  YAML/TOML structural validation (including release conditions and exact secret
  placement), blank starter pipeline/test content, root-`main.py` removal, and absence of
  generated prompts and node sidecars.
- **`test_docs_accuracy.py`** — executable deployment-documentation parity for the
  real scaffold tree and starter node count, registered CLI commands, public Python
  imports, target secrets, and configured pipeline path, plus negative drift fixtures.
- **`test_project_root.py`** + **`test_project_gaps.py`** — `get_project_root` walk-up
  behaviour, `is_haute_project`, and the full `resolve_pipeline_file` four-tier fallback
  (`TestResolvePipelineFile`, `TestTomlConfiguredPipeline`, `TestLooksLikePipelineFile`).
- **`test_node_builder.py`** — `NodeBuildHooks`/`wrap_builder` interception semantics.
- **`test_executor_builders.py`** + **`test_codegen_builders*.py`** — per-`NodeType` builder
  and column-contract behaviour; this is shared fixture territory between this component's
  registry and execution-engine/codegen, since all three read the same `NODE_REGISTRY`.
Property/round-trip style coverage (`TestRoundTripDrift` in `test_graph_shape_contracts.py`,
`test_codegen_roundtrip_property.py`) asserts that parse → build → save → parse is stable for
generated graphs.
