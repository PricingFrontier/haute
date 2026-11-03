# Pipeline config roadmap

## Scope

A node's declarative settings are built from decorator arguments and a JSON
sidecar, repaired when they no longer parse, validated before they are
written back, and read by the executor when the node runs. Current behaviour
is specified in [the pipeline-config specification](../pipeline-config/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| PCFG-R09 | Planned | P3 | A node type is declared in one place. |

`PCFG-R09` comes from the
[23 September 2026 codebase review](codebase-review-2026-09-23.md).

## Planned improvements

### PCFG-R09 — A node type is declared in one place
**Why:** `NODE_REGISTRY` holds the runtime builder, code generator, column
contract and a few flags. Everything else about a node type is special-cased
by type across the codebase: `NodeType.RATING_STEP` alone appears in 17
backend modules, including projection, chunking, RAM estimation, trace
correlation, config building and IO, recovery, the assistant catalogue and
the save service. Each node type also has two semantic implementations, a
runtime closure and a source template.

**Plan:** Grow the registry entry into a node specification that owns the
config model (each node type's `NodeConfigModel` in
`src/haute/_node_config_models.py::NODE_CONFIG_MODELS`, which it reads rather than
duplicates), builder, code template, contract, projection
transfer, trace enrichment and assistant description, and move per-type
branches into it one concern at a time. Where the runtime builder and the
code template can share one `apply_*_from_config` helper, have the template
call it rather than restate the semantics.

**Acceptance:** Adding a node type touches its specification module plus the
frontend editor; no backend module outside the registry branches on a
specific node type for a concern the specification owns.

**Dependencies:** None.

**Evidence:** `src/haute/_registry.py::NodeRegistryEntry`;
`src/haute/_registry.py::NODE_REGISTRY`; `src/haute/_builders.py`;
`src/haute/_codegen_builders.py`; `src/haute/projection.py`;
`src/haute/_trace_correlation.py`; `src/haute/assistant/_catalog.py`.
