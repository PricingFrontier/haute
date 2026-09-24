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
| PCFG-R06 | Decision | P2 | One stated rule for non-canonical input, and code that follows it. |
| PCFG-R07 | Planned | P2 | Every node type has a typed config model that is the single validation boundary. |
| PCFG-R08 | Planned | P3 | Editor-only state travels beside the node config, not inside it. |
| PCFG-R09 | Planned | P3 | A node type is declared in one place. |

`PCFG-R04` to `PCFG-R09` come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).
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

**Plan:** Resolve one immutable project context (root, pipeline file,
parsed `haute.toml`) once at CLI or server start through the specified
four-tier chain, and pass it explicitly or through one accessor. Delete the
other resolvers and the current-directory fallbacks.

**Acceptance:** One resolver remains; starting the server outside a project
fails with the specified error; tests that change directory mid-process no
longer change which project the server serves; the identical helper copies
are gone.

**Dependencies:** None in the roadmap, but most resolvers sit in files owned
by the execution, caching, optimiser and assistant work (`executor`, `_cache`,
`routes/input_cache`, `_input_preparation`, `_data_points`, `assistant`), and
the worker entry points set the sandbox root per request. Two questions come
first: how the in-process context reaches isolated workers, and how tests
select a project once the current directory no longer does (hundreds of
tests `chdir` into a temporary project today).

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

### PCFG-R06 — One rule for non-canonical input
**Why:** The specification README says the implementation "has no branches or
diagnostics that recognise historical Haute input". The code recognises
retired keys in config recovery (`baseInput`, `joinInput`, `scored_input`,
`factors_input`), retired Edge Join decorator arguments, removed config keys
in validation, and legacy modelling `split`/`cross_validation` objects in two
places. Projection synthesises identity "for legacy callers". The assistant
keeps a legacy catalogue and examples (removed by `ASSIST-R01`, which applies
this rule). Explore display validators preserve
unknown keys so "a newer UI can round-trip through an older parser". The
parse-time contract check falls
back to an opaque contract on `ConfigError`, `OSError`, `ImportError`,
`RuntimeError` or `MlflowException`, which this component's own specification
calls broader than infrastructure-only failure.

**Plan:** Decide the rule and write it in the specification README, for
example: targeted rejection messages for removed fields are allowed; no
migration, no silent drop, no forward-compatibility passthrough; fallbacks
only for named infrastructure failures. Then make each listed site follow it,
and give the modelling legacy check one home.

**Acceptance:** The README states the rule; each listed site other than the
assistant's either follows it or is removed; one test per site pins the
behaviour; the legacy modelling check exists once.

**Dependencies:** None. `ASSIST-R01` (assistant) and `SUB-R01`
(submodels) depend on the rule this package decides.

**Evidence:** `src/haute/_node_config_recovery.py::reconcile_config`;
`src/haute/_edge_join.py::_LEGACY_ROLE_DECORATOR_ARGS`;
`src/haute/_config_validation.py::reject_removed_config_keys`;
`src/haute/modelling/_train_config.py::build_training_job_kwargs`;
`src/haute/routes/_training_lifecycle.py::_validate_config`;
`src/haute/projection.py::_projection_edges`;
`src/haute/_explore_overview.py::validate_explore_overview`;
`src/haute/_config_builder.py::_is_contract_resolve_fallback_exception`.

### PCFG-R07 — Typed config models per node type
**Why:** `NodeData.config` is `dict[str, Any]`. The shared validator is strict
only for Data Input, Data Output and Banding; every other type is validated
piecemeal by the save service, the optimiser and training services, the
recovery validators and the runtime builders, and the `TypedDict`s only drive
a key allowlist. Config shape is therefore defined in several places, and the
browser's copy is written by hand.

**Plan:** Define one Pydantic model per node type, discriminated by
`nodeType`, and make it the validation boundary for parse, save, recovery and
execution. Derive the sidecar allowlist from the models, and generate the
browser types from them through `API-R03`.

**Acceptance:** Every node type has a model; the scattered per-type
validators are either deleted or called only from the model's validators; an
invalid config for any node type fails at save with the model's message; the
frontend node-config types are generated.

**Dependencies:** `PCFG-R03`, `PCFG-R08`; `API-R03` (server API)
for generated browser types.

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
