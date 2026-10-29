# Pipeline config roadmap

## Scope

A node's declarative settings are built from decorator arguments and a JSON
sidecar, repaired when they no longer parse, validated before they are
written back, and read by the executor when the node runs. Current behaviour
is specified in [the pipeline-config specification](../pipeline-config/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCFG-R04 | Planned | P2 | One project context, resolved once, replaces a dozen project-root and pipeline-directory resolvers. |
| PCFG-R07 | Planned | P2 | Every node type has a typed config model that is the single validation boundary. |
| PCFG-R08 | Planned | P3 | Editor-only state travels beside the node config, not inside it. |
| PCFG-R09 | Planned | P3 | A node type is declared in one place. |
| PCFG-R12 | Planned | P2 | A save keeps every authored comment, or reports the ones it would drop before it proceeds. |
| PCFG-R14 | Planned | P3 | Both save paths write the same canonical sidecar bytes, the configured pipeline path stays inside the project, and no placeholder file name reaches an error. |
| PCFG-R15 | Planned | P3 | The unreachable config branches, the empty `_load_error` mechanism, the other dead symbols, the five duplicated mechanisms and the stale comments are gone. |
| PCFG-R16 | Decision | P3 | The `earlier` step spelling is either dropped or named as the one allowed recogniser of old generated code. |
| PCFG-R19 | Planned | P3 | Recovery ids are minted once so a diagnostic's "go to" finds its element, the revision captures cover sidecars and configs, a preamble `match` binds names the naming rule sees, and three duplicated recovery mechanisms exist once. |
| PCFG-R21 | Planned | P1 | A parse/save round trip preserves supported keyword-only bindings or refuses an unsupported signature before losing it. |

`PCFG-R04` to `PCFG-R09` come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md);
`PCFG-R12` to `PCFG-R19` from the
[25 October 2026 codebase review](codebase-review-2026-10-25.md).
`PCFG-R08` should precede `PCFG-R07` so the models do not have to carry
editor state.

## Planned improvements

### PCFG-R04 — One project context
**Why:** About a dozen functions resolve the project root or pipeline
directory, with different rules, several of them from the process's current
directory: the sandbox root is the current directory captured on first use;
the project module walks up for `haute.toml` and git, which is the resolver
the specification describes; runtime execution uses a context variable; the
route helper `pipeline_dir` returns the current directory even when
`haute.toml` is missing, only logging an error, which contradicts this
component's rule that not being inside a project raises; the input-cache
routes use the current directory; node data uses the sandbox root; and
builders, hosted storage, MLflow settings and the assistant each have their
own. `executor._pipeline_dir` and `_cache._pipeline_dir` are identical copies.
One step is taken: `pipeline_dir` reads `[project].pipeline` through
`_project._toml_configured_pipeline`, the reader the builders already use,
instead of parsing `haute.toml` itself.

**Decided (24 September 2026):**
- *Workers.* The project context is a small frozen, picklable value (the
  project root and the resolved pipeline file) resolved once at CLI or server
  start. Every worker request carries it in place of today's bare
  `project_root` string, and the worker installs it for the length of the
  request through the one accessor, a context manager over a `ContextVar` (the
  mechanism `_path_resolution` already uses for the runtime root). Nothing is
  inherited from the parent and no global is mutated, so concurrent requests
  in a warm pool cannot see each other's project; `set_project_root` goes.
  Settings the server can change while running (the `[mlflow]` table that
  `PUT /api/mlflow/settings` writes) are not frozen into the context: they
  are read from the context root's `haute.toml` when used, as the MLflow
  destination resolver does today, so the next request sees an update.
- *Tests.* A `project` fixture builds the context for `tmp_path` and installs
  it through the same accessor, and the application is built for a context
  (`create_app(context)`), so a `TestClient` serves exactly that project.
  Modules migrate one at a time; a ratchet like the write-sandbox lint counts
  the remaining `chdir` sites so the number only falls. There is no
  compatibility shim: the current-directory fallbacks are deleted when the
  ratchet reaches zero.

**Plan:** Specify the context and its accessor in the pipeline-config and
server-api specifications. Add the context value, the accessor, the `project`
fixture, `create_app(context)` and the `chdir` ratchet; carry the context in
worker requests; move each resolver's callers onto the accessor and delete the
resolver. Migrate the test modules off `chdir` (about 160) in batches, then
delete the current-directory fallbacks.

**Acceptance:** One resolver remains; starting the server outside a project
fails with the specified error; a server started for project A still serves
A after the test changes directory to project B; worker requests carry the
context and no worker mutates a process global; a worker request made after
an MLflow settings update uses the new destination; the `chdir` ratchet is at
zero; the identical helper copies are gone.

**Dependencies:** `ROAD-WORKER-05` (background jobs), so the worker request
format changes once. Most resolvers sit in files the execution, caching,
optimiser and assistant work also touch (`executor`, `_cache`,
`routes/input_cache`, `_input_preparation`, `_data_points`, `assistant`).

**Evidence:** `src/haute/_project.py::get_project_root`;
`src/haute/_sandbox.py::_get_project_root`;
`src/haute/_path_resolution.py::current_runtime_project_root`;
`src/haute/routes/_helpers.py::pipeline_dir`;
`src/haute/routes/input_cache.py::_project_root`;
`src/haute/routes/_node_data_service.py::node_data_project_root`;
`src/haute/_builders.py::_configured_pipeline_dir`;
`src/haute/_project_storage.py::resolve_project_dir`;
`src/haute/modelling/_mlflow_settings.py::_project_root_default`;
`src/haute/assistant/_config.py::_normalise_project_root`;
`src/haute/executor.py::_pipeline_dir`; `src/haute/_cache.py::_pipeline_dir`.
The interactive pool's targets carry no root at all
(`src/haute/routes/pipeline.py::_execute_preview_worker`,
`_execute_trace_worker`,
`src/haute/routes/_optimiser_worker.py::optimiser_estimate_worker`): a worker
resolves it from its own working directory, which differs from the server's
whenever the root was set without changing directory. The process-mode CI lane
(`backend-process-mode`) shows it: a test whose root is set that way fails there.

### PCFG-R07 — Typed config models per node type
**Why:** `NodeData.config` is `dict[str, Any]`. The shared validator is strict
only for Data Input, Data Output, Banding and the Scenario Expander's grid
size; every other type is validated
piecemeal by the save service, the optimiser and training services, the
recovery validators and the runtime builders, and the `TypedDict`s only drive
a key allowlist. Config shape is therefore defined in several places, and the
browser's copy is written by hand.

**Plan:** Define one Pydantic model per node type, discriminated by
`nodeType`, and make it the validation boundary for parse, save, recovery and
execution. Derive the sidecar allowlist from the models, and generate the
browser types from them through `API-R03`. Generate the key column of the
node-reference "In the pipeline file" tables (`docs/building-models/nodes/`)
from the models, so `tests/test_node_reference_docs.py` checks generated keys
instead of hand-written ones.

**Acceptance:** Every node type has a model; the scattered per-type
validators are either deleted or called only from the model's validators; an
invalid config for any node type fails at save with the model's message; the
frontend node-config types and the node-reference config tables are generated.

**Dependencies:** `PCFG-R08`; `API-R03` (server API) for generated browser
types.

**Evidence:** `src/haute/_types.py::NodeData`;
`src/haute/_config_validation.py::validate_node_config`;
`src/haute/routes/_save_pipeline.py::_validate_strict_node_configs`;
`src/haute/routes/_training_lifecycle.py::_validate_config`;
`src/haute/routes/_optimiser_service.py::_validate_config`.

### PCFG-R08 — Editor state leaves the node config
**Why:** Transient editor and runtime state is stored inside the node config:
in the browser `_columns`, `_nodeId`, `_schemaWarnings`, `_availableColumns`,
`_instance` and step errors; on the backend `_load_error`, `_steps_error`,
`_steps_discarded` and `_discarded_sidecar`. It is then stripped again by
exclusion lists in the browser's config hash, the backend cache-field
classification, the sidecar allowlist and code generation.

**Plan:** Carry editor state in a separate field beside `config` in the graph
and editor-document models, and move every reader and writer to it. Delete
the exclusion lists.

**Acceptance:** No persisted or hashed node config contains an
underscore-prefixed editor key; the exclusion lists are gone; editor
behaviour that depends on this state is unchanged under test.

**Dependencies:** None. `CACHE-S25` (caching) builds on it.

**Evidence:** `frontend/src/stores/useNodeResultsStore.ts::hashConfig`;
`src/haute/_cache.py::_classify_config_fields`;
`src/haute/_config_io.py::_prepare_config_for_sidecar`;
`src/haute/_types.py::NodeData`.

### PCFG-R09 — A node type is declared in one place
**Why:** `NODE_REGISTRY` holds the runtime builder, code generator, column
contract and a few flags. Everything else about a node type is special-cased
by type across the codebase: `NodeType.RATING_STEP` alone appears in 17
backend modules, including projection, chunking, RAM estimation, trace
correlation, config building and IO, recovery, the assistant catalogue and
the save service. Each node type also has two semantic implementations, a
runtime closure and a source template.

**Plan:** Grow the registry entry into a node specification that owns the
config model (`PCFG-R07`), builder, code template, contract, projection
transfer, trace enrichment and assistant description, and move per-type
branches into it one concern at a time. Where the runtime builder and the
code template can share one `apply_*_from_config` helper, have the template
call it rather than restate the semantics.

**Acceptance:** Adding a node type touches its specification module plus the
frontend editor; no backend module outside the registry branches on a
specific node type for a concern the specification owns.

**Dependencies:** `PCFG-R07`.

**Evidence:** `src/haute/_registry.py::NodeRegistryEntry`;
`src/haute/_registry.py::NODE_REGISTRY`; `src/haute/_builders.py`;
`src/haute/_codegen_builders.py`; `src/haute/projection.py`;
`src/haute/_trace_correlation.py`; `src/haute/assistant/_catalog.py`.

### PCFG-R12 — Comments survive a save
**Why:** `_render_module` assembles the file from the preamble, the constructor, the
preserved blocks, each node's source and regenerated `connect` calls, so every save
drops a module-level comment between node functions, a comment between a decorator
and its `def`, a comment after a body's last statement and a comment on a
`connect()` line (probed, five kinds); the only loss detector,
`unkept_module_statements`, walks `tree.body` and cannot see comments, so the
document reads ready and the save proceeds. The codegen specification lists the
statements regeneration keeps and says nothing of comments.

**Plan:** Either report comment-only lines outside the kept regions as a diagnostic
of the `unkept_module_statement` kind (a tokenizer pass over the gaps between kept
spans), or carry them: attach comments between functions to the following function
as leading lines and keep trailing body comments inside the extracted code. State
the rule in the codegen specification either way.

**Acceptance:** The probe file round-trips with its five comments kept, or the
document reports each as unkept with its line and the save is refused until it is
moved.

**Dependencies:** None. The statement check runs over submodel files as well as the
pipeline file, so a comment check built beside it covers both.

**Evidence:** `src/haute/codegen.py::_render_module`;
`src/haute/_ast_helpers.py::_function_body_source`;
`src/haute/_ast_helpers.py::unkept_module_statements`.

### PCFG-R14 — Canonical, complete sidecar bytes and contained paths
**Why:** The whole-document save writes a `contract` copy into every sidecar whose
config carries one, while Recover, Reset and the node-scoped save omit it on purpose
("a sidecar copy is what goes stale"), so the two save paths write different sidecar
shapes and the on-disk copy lags the decorator. Node sidecar JSON is written in the
config dict's insertion order, so the same configuration serialises to different
bytes depending on how the sender ordered its keys; only the global constants file
is canonicalised. An absolute `[project].pipeline` in `haute.toml` binds a file
outside the project with no containment check. `parse_pipeline_source` with a base
directory but no `source_file` invents `<base_dir>/__source__.py`, which then
appears in errors and logs as a path that does not exist.

**Plan:** Strip `contract` in `_prepare_config_for_sidecar`; sort the keys (or apply
a per-type key order) at the sidecar write boundary, re-canonicalising existing
sidecars once; require the configured pipeline path to resolve inside the project
root; leave `source_file` empty when none is known.

**Acceptance:** Both save paths write byte-identical sidecars for one configuration
whatever key order was sent; a `haute.toml` naming a pipeline outside the project
fails with `ConfigError`; no log or error names `__source__.py`.

**Dependencies:** None.

**Evidence:** `src/haute/_config_io.py::collect_node_configs`;
`src/haute/_config_io.py::_prepare_config_for_sidecar`;
`src/haute/_pipeline_repair_actions.py::_reset_node`;
`src/haute/_project.py::_toml_configured_pipeline`;
`src/haute/parser.py::parse_pipeline_source`.

### PCFG-R15 — Dead branches, dead paths, clones and stale comments
**Why:** Every folder-backed branch of `_build_node_config` is unreachable from
production (the sidecar branch raises first) and is kept alive by eight test files;
several convert an inline decorator format the parser refuses. The `_load_error`
mechanism (its formatter, the graph warning, the `rf_nodes` scan, the
`collect_node_configs` skip and `config_load_errors`, which the save service calls)
can only ever produce an empty result because nothing in `src/` sets the key;
`_get_docstring`, `_is_pipeline_node_decorator`, `_is_submodel_node_decorator`, the
textual preamble path and its three regexes, the `n_params` parameter, the
`_parser_parameter_names` attribute and the `func_bodies` parameter have no
production reader. Five mechanisms are written more than once: the
load-document-at-revision preamble (four times), the single-`df`-assignment check
(twice), the receiver call-chain walk (five), the module-level constructor scan
(three) and the preserve-marker scanner (two). Comments and three specifications
still describe the deleted regex recovery and a codegen that post-edits source, and
name functions nothing calls.

**Plan:** Reduce `_build_node_config` to the Edge Join, Explore and transform
branches and move the tests onto sidecar fixtures; delete the `_load_error`
mechanism with the save service's check and the specification sentences; delete the
other dead symbols and their tests; one helper per duplicated mechanism; align the
comments and the codegen and expression-parsing specifications with the AST-only
loader.

**Acceptance:** vulture and the quoted greps show none of the named symbols
unreferenced; jscpd reports none of the pairs; the specifications name no function
that does not exist.

**Dependencies:** `ENGQ-R01` owns dead code outside this component; `PCFG-R09` owns
the per-type branching that remains.

**Evidence:** `src/haute/_config_builder.py::_build_node_config`;
`src/haute/parser.py`; `src/haute/_config_io.py::config_load_errors`;
`src/haute/_ast_helpers.py`; `src/haute/_graph_builders.py`;
`src/haute/_pipeline_repair.py`; `src/haute/_pipeline_repair_actions.py`;
`src/haute/_code_extraction.py`; `src/haute/_parser_submodels.py`;
`specs/codegen/low-level.md`; `specs/expression-parsing/high-level.md`.

### PCFG-R16 — The `earlier` step spelling
**Why:** The parser recognises the renderer's earlier call spelling so bodies saved
by an older Haute keep their steps; the canonical-only policy in `specs/README.md`
names old generated-code recognition among the mechanisms production code must not
have, while the pipeline-config specification documents the spelling as the intended
reconcile. The two documents contradict each other.

**Plan:** Decide: drop the `earlier` spelling (a body in the old spelling then
discards its steps and the user re-saves), or amend the policy to name this one
allowed recogniser and its retirement date.

**Acceptance:** The policy and the pipeline-config specification agree, and the code
matches them.

**Dependencies:** None.

**Evidence:** `src/haute/_config_builder.py::_reconcile_steps`;
`src/haute/_polars_steps.py::StepSpelling`; `specs/README.md`;
`specs/pipeline-config/low-level.md`.

### PCFG-R19 — Recovery ids, captures, `match` bindings and clones
**Why:** Node and occurrence diagnostics are created with a `recovery_id` minted over
their own subset and `_build_recovery_graph` then re-mints every id over the combined
set, so when a node and a submodel occurrence share an authored name the diagnostic's
`element_id` names an element that no longer exists and the banner's "go to" selects
nothing (probed). `_SourceCaptures` promises that every later consumer, the revision
included, reuses the first read, but it covers only the `.py` sources and the
constants file: the editor sidecar is read twice and every node config on its own,
and the revision re-reads from disk anything absent from `known_bytes`, so the
`source_revision` can authenticate sidecar bytes that differ from the ones the
document shows. `_support_code_names` never recurses into a preamble `match`
statement (its children are `match_case` nodes, not statements), so a node named
after a helper bound in a `case` body is not refused and the standalone run rebinds
it; a 3.12 `type` alias is missed the same way. Three mechanisms are written twice:
the file-to-recovery-graph pass for a child file and the root, the step-kind to
input-field table, and the editor-sidecar read with its diagnostic.

**Plan:** Mint the recovery ids once over the combined candidate set before resolving
candidates; route the sidecar and node-config reads through the captures; recurse
into `match_case` bodies and yield their capture names (and `TypeAlias.name`), and
say `match` in the specification; one `_recover_file_graph`, one input-field table,
one `_read_editor_sidecar`.

**Acceptance:** The probe's "go to" selects the node; a sidecar changed between the
capture and the revision is caught by the revision; a node named after a
`match`-bound helper is refused by name; jscpd reports none of the three pairs.

**Dependencies:** `PCFG-R15` owns the first pass's clones.

**Evidence:** `src/haute/_pipeline_recovery.py::_build_recovery_graph`;
`src/haute/_pipeline_recovery.py::_SourceCaptures`;
`src/haute/_pipeline_revision.py::pipeline_recovery_revision`;
`src/haute/_support_code_names.py::support_code_inventory`;
`src/haute/_polars_steps.py::rename_step_inputs`;
`src/haute/_polars_steps.py::step_input_references`.

### PCFG-R21 — Node signatures retain their configuration bindings

**Why:** The parser retains keyword-only names temporarily but drops defaults and the canonical
graph loses the signature. Codegen rebuilds parameters from edges. A valid node with
`factor=1.2` and a same-named preamble value `factor=0.9` computes 120 before Save and 90 after
real parse/codegen. Without the preamble binding it fails at execution. The specification calls
these parameters configuration, yet existing tests check only that no implicit edge is added.

**Plan:** Define supported configuration parameter bindings in the pipeline-config
representation and preserve them through parser, recovery, codegen and standalone execution.
Any signature the representation cannot faithfully preserve must receive a located refusal
before healthy load/Save; never silently drop a default or redirect the name to the preamble.

**Acceptance:** Execute the original and regenerated node with a shadowing preamble constant
and assert equal results. Cover literal defaults, required keyword-only parameters and
unsupported expressions according to the defined contract. Refused forms leave the Python bytes
unchanged. An edge-count assertion alone is insufficient.

**Dependencies:** None.

**Evidence:** `src/haute/_graph_builders.py::_extract_decorated_node_skeletons`; `src/haute/_graph_builders.py::_resolve_node_skeleton`; `src/haute/codegen.py`; `tests/test_parser_conservation.py::TestParameterBucketConservation`.
