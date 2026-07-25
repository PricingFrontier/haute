# Pipeline Config — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/pipeline.py` | `Node` / `NodeRegistry` / `Pipeline` / `Submodel`: the decorator API, `connect()`, the standalone `run()`/`score()` executor, `to_graph()` (live-object → React-Flow dict). |
| `src/haute/_config_builder.py` | Per-node-type config dict construction from decorator kwargs + function body (`_build_node_config`); sidecar resolution and the parse-time `contract=` cross-check (`_resolve_node_config`). `config["inputs"]` records the function's parameter names verbatim — under input-identity convergence these ARE the per-edge input names (`edge_input_name`: frame labels for apiInput edges, sanitised source labels otherwise), the same strings `input_scenario_map` keys and instance `inputMapping` values reference. |
| `src/haute/_config_io.py` | Sidecar JSON path conventions (`NODE_TYPE_TO_FOLDER`), read/write helpers, `collect_node_configs` (graph → sidecar files), Windows-reserved-filename guard. |
| `src/haute/_config_validation.py` | `VALID_KEYS` registry derived from each node type's `TypedDict`, and `warn_unrecognized_config_keys`. |
| `src/haute/_builders.py` | Cross-component dependency owned by [execution-engine](../execution-engine/low-level.md): registers each per-`NodeType` runtime builder and its column-contract callback in `NODE_REGISTRY`. Pipeline-config consumes those callbacks through `src/haute/_contracts.py`; it does not own the runtime closures. |
| `src/haute/_node_builder.py` | Cross-component dependency owned by [execution-engine](../execution-engine/low-level.md): `NodeBuildHooks` / `wrap_builder` allow deploy scoring to intercept runtime builders. It is listed here to make the boundary explicit, not because sidecar/static graph construction calls it. |
| `src/haute/_contracts.py` | Cross-component contract model and registry-backed `get_column_contract()` lookup used by parse-time `contract=` validation. `Contract`, `ColumnContract`, and `OPAQUE_CONTRACT` are defined here, not in `src/haute/_builders.py`. |
| `src/haute/_registry.py` | Cross-component `NODE_REGISTRY` storage shared by execution and codegen. Pipeline-config reads its column-contract registrations indirectly through `src/haute/_contracts.py`. |
| `src/haute/_graph_builders.py` | AST-derived raw node dicts → `GraphNode`/`GraphEdge` Pydantic models (`_extract_decorated_nodes`, `_build_edges`, `_build_rf_nodes`). |
| `src/haute/_graph_shape.py` | Topology-only invariants independent of any single node's config (`validate_graph_shape_contracts`, `validate_pipeline_graph_shape_contracts`), including submodel child graphs. |
| `src/haute/_scaffold.py` | `haute init` template strings: `haute.toml`, `.env.example`, CI YAML for 3 providers × 7 deploy targets, starter pipeline/tests/utilities, pre-commit hook. |
| `src/haute/_project.py` | Project-root discovery (`get_project_root`, `is_haute_project`) and pipeline-file resolution (`resolve_pipeline_file`, 4-tier fallback). |
| `haute.toml` (repo root) | Concrete instance of the schema `_scaffold.haute_toml()` emits; `[project].pipeline` is read back by `_project._toml_configured_pipeline`. |

## Key types and data structures

- **`Node`** (dataclass, `pipeline.py`) — `name`, `description`, `fn`, `is_source`,
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
  `Pipeline.submodel(file)` call only records the path in `_submodel_files`; it does not import
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

**1. Live Python object graph (`pipeline.py`).** A `Pipeline` instance is built directly by
decorator calls at import time. `Pipeline.run()`/`Pipeline.score(df)` topologically sort the
in-memory `_edges`/`_nodes` via `haute.graph_utils.topo_sort_ids` (raising `CycleError` on a
cycle). With no edges, `_topo_order` returns registration order; it does not create a chain.
Execution then fails at `_execute_transform` if a non-source node has no inbound edge, rather
than inferring one from its parameter names. Otherwise it executes each `Node`'s `fn`, threading
DataFrames along declared edges — each edge's frame resolved port-aware through the shared
`_pick_source_frame` selection on `RegisteredEdge.source_port` — and resolves the return value
through `_resolve_output_node`: an explicit `@pipeline.output` node wins if there is exactly one;
otherwise the single node with no outgoing edge; otherwise raise, naming every candidate node.
`Pipeline.to_graph()` independently converts the same live objects into a React-Flow-shaped
plain `dict`, inferring each node's display type from `config["_node_type"]` if present, else
`DATA_INPUT` for an input node, else `OUTPUT` for the last-registered node, else `POLARS`, and
serialising exactly the registered edges without parameter-name inference.

**2. Static source graph (`_graph_builders.py` + `_config_builder.py`).** Given an
already-parsed AST module and pre-extracted function bodies (produced upstream, not by this
component), `_extract_decorated_nodes` walks top-level `FunctionDef`/`AsyncFunctionDef` nodes
matching the pipeline-decorator checker. For each match it calls `_resolve_node_config`, which
either: loads and normalises a `config=` sidecar via `_config_io.load_node_config` and
attaches code parsed from the function body (`_attach_code_from_body`); raises
`_sidecar_required_error` if the node type is folder-backed but no `config=` was given; or
dispatches into `_build_node_config`'s per-`NodeType` branch to build the config purely from
decorator kwargs + body. `_resolve_node_config` also pops a `contract=` kwarg before
delegating (so per-type builders don't flag it as unrecognised), cross-checks it via
`_validate_user_contract`, and re-attaches it to the config afterwards. The resulting raw node
dicts feed `_build_edges` (explicit `connect()` tuples in 2/3/4-arity legacy/port-aware forms,
plus implicit parameter-name-matching edges; edges are never invented, so a file declaring no
wiring parses as a disconnected graph) and `_build_rf_nodes` (assigns x-spaced GUI positions) to
produce
the final `list[GraphNode]`/`list[GraphEdge]` — the graph the frontend, codegen, and the real
executor operate on.

**Sidecar write path.** `_config_io.collect_node_configs(graph)` walks a `PipelineGraph`,
skips node types without a config folder, instance nodes (`config["instanceOf"]` set), and
nodes flagged `config["_load_error"]` (protects the on-disk file from a bad in-memory state
clobbering it), remaps `optimiserApply.ratebook_input` from a GUI node id to the codegen-
stable id via `_remap_config_ids_for_saved_graph` (logging a WARNING and leaving the config
unchanged if the referenced upstream node can't be resolved), filters each config through
`_prepare_config_for_sidecar` (strips `code`/`_`-prefixed keys recursively via
`_strip_internal_keys`, applies the `VALID_KEYS` allowlist — logging any dropped keys at
WARNING — then per-type compaction for `BANDING`/`RATING_STEP`), and serialises the result to
`{relative_path: json_string}`.

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

**Scaffold generation (`_scaffold.py`, driven by `cli/_init_cmd.py::handle_init`).** Every
template function is parameterised by `target`/`ci` and looks up per-target facts through
`TARGETS`/`_get_target` rather than branching on the target string directly, so adding a
target means adding one registry entry, not touching every template function.
The starter Data Input sidecar uses the traversal-free pipeline-relative path
`data/sample.parquet`, and `handle_init` creates the matching `rating/data/` directory.
It never writes a `..` segment that direct generated-function execution would reject.
`haute_toml()` assembles `[project]`/`[deploy]`/`[test_quotes]`/`[safety]`/`[safety.approval]`
(`min_approvers` hardcoded to 2 in the template — solo users lower it by hand)/`[ci]`/
`[ci.staging]` sections, splicing in `_target_section()`'s `[deploy.<target>]` block.
`env_example()` and the three CI-YAML generators (`github_ci_yml`/`github_deploy_yml`/
`github_deploy_prod_yml`, `gitlab_ci_yml`, `azure_devops_yml`) all pull the same `secrets`
list out of `TARGETS` through provider-specific formatters (`_github_secrets_env`,
`_gitlab_secrets_env`, `_azure_devops_secrets_env`) so a target's credential list is defined
once and rendered three ways. `handle_init` calls exactly the generator(s) for the chosen
`ci` value (`"none"` calls none of them); on `--force` it first calls
`_prune_stale_ci_files(project_dir, keep=ci)`, which deletes every other provider's known
artifact paths (from the `_CI_ARTIFACTS` map) and removes the resulting empty
`.github/workflows/`/`.github` directories, before writing the new provider's files.

## Edge cases and invariants

- The live `to_graph()` path and the static `_graph_builders` path can disagree on node
  typing. `to_graph()` infers `OUTPUT` for "the last registered node" whenever no node has an
  explicit `_node_type` and it isn't a source; the static path only ever assigns `OUTPUT` from
  an explicit `@pipeline.output` decorator.
  > NOTE: for an all-`@pipeline.polars` pipeline with no explicit `@pipeline.output`, both
  > paths happen to treat the last/only-leaf node as the output — but for different reasons —
  > and they diverge for a graph with several leaves and no explicit output: `run()` raises
  > naming every leaf, while `to_graph()` still assigns some type to the last-registered node
  > without checking degree at all.
- A static `pipeline.connect()` naming an unknown source or target is silently omitted by
  `haute._graph_builders._build_edges`; the live `Pipeline.connect()` rejects the same mistake
  immediately. There is no graph-level warning for the static omission.
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
- `_toml_configured_pipeline` swallows every exception from `tomllib.load` (malformed TOML)
  and returns `None` rather than surfacing a syntax error — `resolve_pipeline_file` never
  reports a TOML parse failure itself, only a missing or non-pipeline-looking configured path.
  A genuinely malformed `haute.toml` is instead caught elsewhere (e.g. `DeployConfig.from_toml`,
  outside this component).
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
- A polars node with a self-contained code body and no upstream wiring (`_build_transform` in
  `_builders.py`) is treated as a source (`is_source=True`) rather than requiring an input —
  the code is expected to construct its own frame (e.g. `pl.DataFrame(...)`).
- `_azure_devops_secrets_env` takes an explicit `indent` because the same secrets block is
  spliced into the generated YAML at two different nesting depths: job-level `env:` blocks
  sit at 12 spaces (so keys need 14), while the `DeployProduction` stage's `runOnce.deploy`
  strategy nests `env:` at 18 spaces (so keys need 20). `azure_devops_yml` calls the helper
  twice with different `indent` values for this reason — sharing one indent across both call
  sites would under-indent the production secrets block into unparseable YAML.
  > NOTE: the Azure DevOps workflow's `environment: production` block only names the
  > environment; the approval check itself is configured on that environment inside the Azure
  > DevOps portal, not emitted by `_scaffold.py`. A freshly-scaffolded Azure DevOps project's
  > production stage runs unapproved until a human configures the environment's approval
  > check out-of-band.

## Error handling

- **`ConfigError`** (`haute.errors`) — missing/unreadable/invalid-JSON sidecar; sidecar
  content failing schema validation; a folder-backed node type used without `config=`;
  `optimiserApply` misconfiguration (`artifact_path` set without `sourceType`); `modelScore`
  misconfiguration (blank `run_id`/`registered_model` for the declared `sourceType`); project
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
  `instanceOf`/`inputMapping` references in the standalone executor; a bare-frame `score()`
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

Tests live under `tests/`, predominantly as behavioural unit tests against the real decorator
API and real JSON round-trips rather than mocks:

- **`test_pipeline.py`** (80 tests) — `Node`/`Pipeline`/`Submodel` decorator registration,
  arity validation (`TestNodeArityValidation`), edge wiring and topo-order delegation, output
  resolution (`TestOutputResolution`), duplicate-name rejection
  (`TestDuplicateNodeName`), instance-reference fail-loud behaviour
  (`TestInstanceReferencesFailLoud`), the API-input deploy-seed marker
  (`TestApiInputDecoratorMarksSeed`), and `to_graph()` shape/inference (`TestPipelineEdgeCases`
  and scattered `to_graph` tests across other classes). The input-identity release adds the
  full `score()` seed matrix here: bare frame accepted at zero ports (source-only) and one
  port; bare frame rejected at 2+ ports; exact one-key dict accepted at one port; dict
  rejected with missing keys, with unknown extra keys, and against a zero-port source — every
  rejection an `ExecutionError` naming the ports — plus `run()` port-aware frame selection
  for one- and many-frame apiInput sources.
- **`test_config_io.py`** + **`test_config_io_gaps.py`** (~97 tests) — sidecar save/load
  round-trips, path conventions (`TestConfigPathForNode`), Windows-reserved-filename rejection
  (`TestIsWindowsReservedFilename`), `collect_node_configs` (including load-error protection
  and id remapping), and banding/rating-step sidecar compaction/expansion.
- **`test_config_validation.py`** (44 tests) — `VALID_KEYS` registry completeness
  (`TestValidKeysRegistry`), `warn_unrecognized_config_keys` behaviour, and alignment between
  each type's decorator kwargs and the config keys `_build_node_config` actually produces
  (`TestBuildNodeConfigProducesValidKeys`, `TestConfigKeyTupleAlignment`).
- **`test_parser_helpers.py`** + **`test_parser_helpers_split.py`** +
  **`test_parser_helper_patch_targets.py`** — AST extraction (`TestExtractDecoratedNodes`),
  decorator kwarg parsing, `_build_node_config` per node type
  (`TestBuildNodeConfigExtended`), `_resolve_node_config` sidecar and contract paths
  (`TestResolveNodeConfig`), and edge/GraphNode building (`TestBuildEdges`,
  `TestBuildRfNodes`), including the deliberate static omission of connects whose endpoint is
  not a parsed node.
- **`test_graph_shape_contracts.py`** (14 tests) — Explore in/out-degree contracts
  (`TestExploreGraphShape`), single-node and empty-graph edge cases, submodel boundary handle
  matching, and round-trip drift (`TestRoundTripDrift`).
- **`test_scaffold.py`** — every CI provider × deploy target combination, YAML/TOML
  structural validation, and starter pipeline/test content.
- **`test_project_root.py`** + **`test_project_gaps.py`** — `get_project_root` walk-up
  behaviour, `is_haute_project`, and the full `resolve_pipeline_file` four-tier fallback
  (`TestResolvePipelineFile`, `TestTomlConfiguredPipeline`, `TestLooksLikePipelineFile`).
- **`test_node_builder.py`** — `NodeBuildHooks`/`wrap_builder` interception semantics.
- **`test_executor_builders.py`** + **`test_codegen_builders*.py`** — per-`NodeType` builder
  and column-contract behaviour; this is shared fixture territory between this component's
  registry and execution-engine/codegen, since all three read the same `NODE_REGISTRY`.
- **`test_strict_v2_contract.py`** — pins the write-time `VALID_KEYS` allowlist and the
  `apiInput` legacy-key stripping behaviour.

Property/round-trip style coverage (`TestRoundTripDrift` in `test_graph_shape_contracts.py`,
`test_codegen_roundtrip_property.py`) asserts that parse → build → save → parse is stable for
generated graphs.

> Known gap: the live `Pipeline.to_graph()` path and the static `_graph_builders.py` path are
> exercised by separate test files with no explicit cross-check asserting they produce
> equivalent graphs for the same source pipeline — consistent with the type-inference
> divergence noted above under Edge cases. The static unknown-endpoint omission is unit-tested
> as current behaviour, but no end-to-end test requires it to produce a visible warning.

## Polars backend contracts (0.6.0)

Remaining pipeline-configuration improvement work is tracked in the
[pipeline authoring roadmap](../../roadmap/pipeline-authoring.md).

`Node` computes `_InputArity` exactly once from `inspect.signature(fn)` during registration or
construction and stores it as immutable node state; execution consumes that stored result rather
than inspecting the callable again. `POSITIONAL_ONLY` and `POSITIONAL_OR_KEYWORD` parameters
are supported edge inputs, with defaults making them optional. `KEYWORD_ONLY` parameters are
never edges. `VAR_POSITIONAL` (`*args`) is the only supported unbounded form, after required
positional inputs. Unsupported callable signatures and all arity/wiring mismatches raise loudly
with node name and expected arity.

Live-switch execution distinguishes an absent mapping from a present mapping. Without a mapping,
default-source fallback remains permitted. With one, lookup of the active scenario is mandatory:
a missing key raises `LiveSwitchScenarioError(ExecutionError)` and never silently selects the
default source. The exception exposes stable code `live_switch_scenario_missing` plus stable
`switch`, `scenario`, and `available_mappings` fields; `available_mappings` is deterministic so
the same configuration produces the same diagnostic. HTTP translation returns 422, while a
background run records `contract_error` with the same code and fields.

Focused tests cover one-time signature inspection, required/optional/`*args` arity, malformed
wiring, unconfigured live-switch fallback, configured matching selection, and configured
mappings missing the active scenario, including exact exception inheritance/code/fields and
HTTP/background translation. The 0.6 pre-1.0 migration note documents the newly loud configured
mapping miss. Non-goals: implicit wiring inference, additional variadic forms, changes to
successful mapped live-switch selection, or removal of unconfigured default-source fallback.

## Approved change contract — 0.7.0 canonical data I/O node types

Remaining pipeline-configuration improvement work is tracked in the
[pipeline authoring roadmap](../../roadmap/pipeline-authoring.md).

- In `src/haute/_types.py`, delete `NodeType.DATA_SOURCE`, `NodeType.DATA_SINK`,
  `DataSourceConfig`, and `DataSinkConfig`; extend `DataInputConfig`/`DataOutputConfig` with the
  exact discriminated fields in the I/O low-level contract. Remove `"data_source"` and
  `"data_sink"` from `DECORATOR_TO_NODE_TYPE`.
- In `src/haute/pipeline.py`, remove `NodeRegistry.data_source()` and `.data_sink()`. Keep
  `.data_input()`/`.data_output()` as ordinary registration wrappers; the live API may register
  multiple instances. Preserve `_resolve_output_node` semantics: multiple terminal Data Outputs
  without one explicit `NodeType.OUTPUT` remain an actionable ambiguous-leaf error.
- In `src/haute/_config_io.py`, delete the two legacy folder mappings and retain
  `config/data_input/` and `config/data_output/`. In `_config_validation.py`, derive strict
  branch-aware validation from the retained TypedDict/validator rather than accepting the union
  of every possible branch key.
- `_config_builder.py` extracts the generated `dataInput` post-read Polars body into `code` and
  validates it as part of the input config. Output body scaffolding never becomes config code.
  `_graph_builders.py` and parser decorator recognition reject removed decorators normally.
- `_scaffold.py`, checked-in rating/reference projects, examples, assistant assets, and every
  pipeline fixture containing a removed decorator are reset to their standard blank-pipeline
  representation. Do not inspect or translate their removed node configs.
- Tests pin the exact enum/decorator/folder/key sets, branch-specific rejection, no inactive-key
  leakage, multiple-node registration, explicit-output and ambiguous-leaf standalone execution,
  config JSON round trips, blank scaffold/reference reset, and source search proving there is no
  executable legacy mapping or alias.

## Retained input sidecar authority

- Generated `apiInput` decorators reference
  `config/quote_input/<name>.json`; generated `externalFile` decorators
  reference `config/load_file/<name>.json`.
- `resolve_api_input_from_config` and
  `load_external_object_from_config` accept either an inline mapping or a
  sidecar path. Path arguments are loaded through `load_node_config` and
  relative data/object paths use `base_dir` as the pipeline candidate in the
  shared project/pipeline resolution policy.
- API-input resolution requires a non-empty path for flat/JSON inputs,
  validates `tables[]` before the JSON shred, and forwards projection/profile
  arguments for flat-file reads. External-file resolution validates non-empty
  `path` and `fileType` strings and forwards `modelClass`.
- Missing or malformed API-input `tables[]` raises the typed
  `ApiInputSchemaError` contract. Execution routes adapt it to a stable 422
  response rather than allowing a bare runtime exception to become a 500.
- Executor builders call the same helpers with their already-resolved inline
  graph config. Generated builders pass only the sidecar path and `base_dir`;
  no declarative field is interpolated into the function body.
