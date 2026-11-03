# Pipeline config roadmap

## Scope

A node's declarative settings are built from decorator arguments and a JSON
sidecar, repaired when they no longer parse, validated before they are
written back, and read by the executor when the node runs. Current behaviour
is specified in [the pipeline-config specification](../pipeline-config/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCFG-R07 | Planned | P2 | Every node type has a typed config model that is the single validation boundary. |
| PCFG-R09 | Planned | P3 | A node type is declared in one place. |

`PCFG-R07` and `PCFG-R09` come from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Planned improvements

### PCFG-R07 — Typed config models per node type
**Why:** `NodeData.config` is `dict[str, Any]`. The shared validator is strict
only for Data Input, Data Output, Banding and the Scenario Expander's grid
size; every other type is validated
piecemeal by the save service, the optimiser and training services, the
recovery validators and the runtime builders, and the `TypedDict`s only drive
a key allowlist. Config shape is therefore defined in several places, and the
browser types the config as an open object.

**Plan:** Define one Pydantic model per node type, discriminated by
`nodeType`, and make it the validation boundary for parse, save, recovery and
execution. Derive the sidecar allowlist from the models, and add them to the
generated browser contract (`scripts/generate_api_contracts.py`), so a
node's `config` in every generated response is the model's type. Generate the key column of the
node-reference "In the pipeline file" tables (`docs/building-models/nodes/`)
from the models, so `tests/test_node_reference_docs.py` checks generated keys
instead of hand-written ones.

**Acceptance:** Every node type has a model; the scattered per-type
validators are either deleted or called only from the model's validators; an
invalid config for any node type fails at save with the model's message; the
frontend node-config types and the node-reference config tables are generated.

**Dependencies:** None.

**Evidence:** `src/haute/_types.py::NodeData`;
`src/haute/_config_validation.py::validate_node_config`;
`src/haute/routes/_save_pipeline.py::_validate_strict_node_configs`;
`src/haute/routes/_training_lifecycle.py::_validate_config`;
`src/haute/routes/_optimiser_service.py::_validate_config`.

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
