# Submodels roadmap

## Scope

Submodel definitions and occurrences, and the other mechanisms for reusing
logic in a pipeline. Current behaviour is specified in
[the submodels specification](../submodels/high-level.md). `SUB-R01`
comes from the [23 September 2026 codebase review](codebase-review-2026-09-23.md).
`SUB-R04` and `SUB-R05`
come from the [25 October 2026 codebase review](codebase-review-2026-10-25.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-R01 | Planned | P3 | One mechanism reuses a node or group of nodes. |
| SUB-R04 | Planned | P2 | A Load File child whose free code names a second input keeps that input across a submodel boundary, as a Polars child does. |
| SUB-R05 | Planned | P3 | The no-op alias rewrite, the hidden-port fields nothing produces, the parse that create and dissolve never read, and the second validation per flatten are gone. |
| SUB-R06 | Planned | P2 | Node instances after multi-frame request sources work in full preview, CLI run and assistant preview as well as target-only preview. |

## Planned improvements

`SUB-R01` changes how a node instance becomes an occurrence, using the existing
import-based registration. `SUB-R01` is best taken before
`PCFG-R08` and `PCFG-R07` (pipeline config), so the editor-state move and the
typed config models never have to carry node-level instances.

### SUB-R01 — One reuse mechanism
**Why:** Four mechanisms overlap. Node-level instances (`@pipeline.instance`,
`instanceOf` with `inputMapping`) reuse one node's logic. Submodel
definitions with instance occurrences reuse a group of nodes, with read-only
copies and port minting that suffixes `_2` and `_3` on collision. Plain
Polars transforms carry `inputMapping` to keep logical input names across
rewires. Project `utility` modules reuse functions. Together they account
for 258 `instanceOf`/`instance_of` references across the backend and
frontend. Files named after submodels hold 6,300 source lines and 11,700 test
lines, and 25 of the 48 submodel commits since May 2026 were fixes or
hardening of identity and aliasing.

**Decided (24 September 2026):** the review's recommendation. A node
instance is a one-node submodel occurrence, so node-level instances go as a
separate mechanism. Submodel definitions with their occurrences and project
`utility` modules are the reuse mechanisms. `inputMapping` on a plain Polars
transform stays: it names a node's inputs across rewires and reuses no logic.
The capability users have today stays: **Create Instance** on a Polars or
Model Score node wraps the node in a one-node submodel definition (its own
`modules/<name>.py`) and places a second occurrence, so editing the
definition still updates every copy.

**Plan:** Specify the kept mechanisms, the one-node submodel form of an
instance and the targeted rejection of `@pipeline.instance` and `instanceOf`
(the canonical-input rule) in the submodels and pipeline-config
specifications. First prove that a one-node submodel carries every current
instance use (Polars and Model Score, with remapped inputs) through preview,
trace, training, codegen round-trip and deploy; if one cannot, stop and bring
the gap back as a decision. Then make **Create Instance** produce the
submodel form, remove the node-instance parser, codegen, executor, flattening
and editor paths, and rewrite `docs/building-models/nodes/instances.md` as
"reusing a node" through submodels.

**Acceptance:** The submodels specification names the supported reuse
mechanisms; `@pipeline.instance` and `instanceOf` are rejected with a message
naming the submodel form; no parser, codegen, executor or editor path handles
node-level instances; **Create Instance** yields a working one-node submodel
occurrence for Polars and Model Score nodes; the documentation describes only
supported mechanisms. The replacement one-node submodel also passes the
[SUB-R06](#sub-r06--multi-frame-sources-work-through-node-instances) regression.

**Dependencies:** None. The canonical-input rule that applies to removed forms
is in the [specification README](../README.md#canonical-only-format-policy).

**Evidence:** `src/haute/pipeline.py::instance`;
`src/haute/_submodel_instances.py`; `src/haute/_flatten.py`;
`docs/building-models/nodes/instances.md`;
`docs/building-models/nodes/submodel.md`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts`.

### SUB-R04 — A Load File child keeps its second input
**Why:** When flattening renames a definition's physical input (the port name
becomes the upstream name), `rewrite_boundary_input_names` records an
`inputMapping` only for a stepped surface or a `POLARS` node, while
`_build_external_file` binds free code by edge name through
`_exec_user_code(..., input_mapping=...)` and the stepped-surface table says a
Load File's other edges stay addressable. A Load File child whose free code
names a second input therefore loses it across the boundary and fails at
execution naming a frame that no longer exists; the specification names only
Polars code as keeping its logical name.

**Plan:** Key the rename branch on "a surface whose code addresses inputs by
name and holds no steps" rather than on `NodeType.POLARS`, so a free-code Load
File child gets the same `inputMapping`; add a flatten test with a Load File
child reading a second input; say so in the specification.

**Acceptance:** A grouped Load File child that names a renamed second input
executes after flattening with the same result as before grouping.

**Dependencies:** None.

**Evidence:** `src/haute/_submodel_instances.py::rewrite_boundary_input_names`;
`src/haute/_builders.py::_build_external_file`.

### SUB-R05 — Dead rewrites, hidden ports and double work
**Why:** `rewrite_submodel_alias_references` is a no-op: its only caller passes
the identity map, so every occurrence is copied through `with_config` for
nothing, and `rewrite_node_references` exists only to serve it. The "hidden
authored port" fields `sourcePort` and `targetPort` are a format nothing
produces: the parser always writes `None`, the browser and the recovery document
only pass a value through, yet every boundary edge identity, the dedupe key, two
port fallbacks and every clone thread them. Create and dissolve parse the whole
on-disk pipeline (hashing every source and sidecar for its revision) and use
nothing from it, since both transform the request graph and take the editor
document's revision. Every flatten validates the graph twice, in `flatten_graph`
and again in `expand_submodel_instances`.

**Plan:** Delete the two rewriters and the parser call; remove the two fields,
the hidden-port branch of the edge identity, the fallbacks and the dedupe
columns, so a boundary edge's identity is the ordinary four-field form; parse the
strict graph only for drill-down; pass the resolved occurrence map into
`expand_submodel_instances`.

**Acceptance:** The named symbols and fields are gone; create and dissolve
perform no pipeline parse (a counting test); one validation per flatten; the
submodel tests pass unchanged.

**Dependencies:** `SUB-R01` removes node-level instances, not these.

**Evidence:** `src/haute/_submodel_instances.py::rewrite_submodel_alias_references`;
`src/haute/_submodel_instances.py::rewrite_node_references`;
`src/haute/_types.py::GraphEdge`; `src/haute/_graph_utils.py::_edge_id`;
`src/haute/routes/submodel.py::_load_parent_document`;
`src/haute/_flatten.py::flatten_graph`;
`src/haute/_submodel_instances.py::expand_submodel_instances`.

### SUB-R06 — Multi-frame sources work through node instances

**Why:** A current instance downstream of a two-frame Quote Input fails ordinary full
preview: the executor's instance schema-warning code treats the upstream bundle dict as
a DataFrame and reads `.columns`. The same graph succeeds with target-only preview.
The full-preview failure was reproduced; CLI run and assistant preview share that path
by source inspection.

**Plan:** Make the instance schema-warning path respect the source's multi-frame contract
and the selected input binding. Preserve the normal input-identity checks; do not invent
a default frame or suppress the failure. Deliver this correctness fix independently of
the reuse migration in SUB-R01.

**Acceptance:** Use the measured policy/risks source with original and copied Polars
consumers. Full preview, CLI run, assistant preview and target-only preview return the
expected rows and columns, without `.columns` on a bundle. The one-node submodel
replacement must pass the same regression when SUB-R01 ships.

**Dependencies:** None; this fix cannot wait for SUB-R01.

**Evidence:** `src/haute/executor.py::_execute_graph_core._cached_output_names`
and its instance schema-warning consumer in `_execute_graph_core`.
