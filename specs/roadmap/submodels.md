# Submodels roadmap

## Scope

The authoring and identity model of submodel definitions and occurrences:
public ports, occurrence registration, and the names that connect, display
and execute them. Current behaviour is specified in
[the submodels specification](../submodels/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-L01 | Planned | P2 | One name per public port: the name the parent connects by is the name everywhere. |
| SUB-L02 | Planned | P2 | One name per occurrence: the canvas shows the alias and renaming it renames the code. |
| SUB-L03 | Planned | P2 | One identity per occurrence: the node id is the name, and a registration is `pipeline.submodel(path, name)`. |

## Planned improvements

Delivery order is `SUB-L01` → `SUB-L02` → `SUB-L03`. All three
follow F13 (`specs/roadmap/bug-findings-2026-09-05.md`), which made the parser
bind every Polars parameter by name with no inference, named an occurrence's
outputs after the occurrence, and bound a definition's port-fed nodes to the
port id. The principle is the one ordinary nodes and API-input frames already
follow: a thing has one name, that name is what you connect by, what the code
reads, and what the canvas shows; labels are not a second identity.

---

### SUB-L01 — One name per public port

**Why:** A public port carries `portId` (what `connect(..., target_port=...)`
names, what the `in__`/`out__` handles encode, and since F13 what a definition
node's parameter binds to) and a separate `label`. The label is display text
that no editor control can change, that grouping and boundary editing mint
from the executable input name anyway, and that was the only place a display
string had ever been promoted to an executable name. Every hand-written
definition in the repository authored the id and ignored the label. Two
spellings of one thing cost an authoring key, a request field, three reverse
maps and a duplicate-label check, and they produced F13.

**Plan:** One coherent change (the model cannot be split):

- Model: `SubmodelInputPort {name, targets}` and `SubmodelOutputPort {name,
  source}` replace `{portId, label, ...}`. A name is a canonical identifier
  (its sanitised form is itself) and unique per definition across both
  directions; handles stay `in__<name>` and `out__<name>`; `connect`'s
  `target_port`/`source_port` take the name.
- Parser and DSL (`haute.Submodel(...)`, `src/haute/_parser_submodels.py`):
  accept `name` only; a declaration carrying `label` or `portId` is a
  `ParseError` naming the key and the replacement. No compatibility shim.
- Codegen: emit `name` only; child-side parameters are the port name (F13).
- Grouping and boundary editing (`create_submodel_graph`,
  `canonicalSubmodelBoundaryEditing.ts`): mint the port name from the
  boundary's sanitised executable input name, suffixing `_2`, `_3` on
  collision, so extracted code keeps working with no mapping; delete
  `_public_frame_label`, the duplicate-label check (name uniqueness replaces
  it), `_inputPortInputNames`, `submodelInputPortIdForName` and every other
  id-to-name reverse map, which become identities.
- Editor identities and recovery: delete the `source_handle_labels` request
  field and its coverage validation; `submodel_output_label`,
  `edge_input_label`, `_port_labels` and `submodel_output_labels` resolve the
  name; the structural fingerprint hashes names only.
- Frontend: port types and document parser keys, `SubmodelPortData`, node and
  frame rows, the port editor badges and boundary diagnostics show the name.
- Sweep: the 61 port declarations across 18 backend test modules, the
  `reusable_submodel` example and its manifest digest, and the twelve spec
  files that describe port labels (submodels, codegen, frontend-graph-canvas,
  frontend-node-editors, server-api, expression-parsing, pipeline-config).
  User documentation under `docs/` does not mention ports and is untouched.

**Acceptance:** A definition declaring `label` or `portId` fails to parse with
a diagnostic that names the key and the fix; every definition fixture and the
example round-trip through codegen and the parser byte-identically; the
group, dissolve and rename browser journeys pass with ports named after the
boundary names; the editor-identity request carries no label field; a
repository contract test finds no `label` in port models, port handles or
generated port declarations; the generated submodel-endpoint family authors
ports by name only; every backend, Vitest and browser suite is green without
weakened gates.

**Dependencies:** F13 landed (strict binding, occurrence-named outputs,
port-id binding).

**Evidence:** `src/haute/_types.py`; `src/haute/_parser_submodels.py`;
`src/haute/codegen.py`; `src/haute/routes/_submodel_ops.py`;
`src/haute/_submodel_instances.py`; `src/haute/_graph_utils.py`;
`src/haute/_editor_identities.py`; `src/haute/_pipeline_recovery.py`;
`frontend/src/types/node.ts`;
`frontend/src/utils/canonicalSubmodelBoundaryEditing.ts`;
`tests/test_submodel_ops.py`; `tests/test_submodel_endpoint_properties.py`.

### SUB-L02 — One name per occurrence

**Why:** `pipeline.submodel(..., label=...)` and the occurrence node's
`data.label` are display-only. Renaming an occurrence in the editor changes
that label and nothing else, so the canvas shows a name the code never uses
while every `connect` line and, since F13, every consumer parameter uses the
alias.

**Plan:** Remove the `label=` keyword (the parser rejects it, codegen stops
emitting it) and make the occurrence node's display name the alias. Renaming
an occurrence in the editor becomes an alias rename handled like an ordinary
node rename: `nodeUpdatePlan` sees the outgoing edge names change from the old
alias to the new, rebinds consumers through `inputMapping` without editing
their code, and Save regenerates the registration and the `connect` lines with
the new alias. The alias validator requires a canonical identifier unique among
the parent's node names; grouping (`sm_name`) and Create Instance
(`nextSubmodelAlias`) already mint aliases that way.

**Acceptance:** Renaming an occurrence updates every `connect` line and keeps
downstream code executing, proven by a backend round trip and a browser
journey; a file carrying `label=` fails to parse loud; codegen never emits
`label=`; the invariant `data.label == config.alias` holds for every
occurrence at parse time and in the editor store.

**Dependencies:** SUB-L01.

**Evidence:** `src/haute/_parser_submodels.py`; `src/haute/codegen.py`;
`frontend/src/utils/nodeUpdatePlan.ts`; `frontend/src/hooks/useNodeHandlers.ts`;
`tests/test_parser_submodels.py`;
`frontend/src/utils/__tests__/nodeUpdatePlan.test.ts`.

### SUB-L03 — One identity per occurrence

**Why:** After SUB-L02 a registration still carries three identities where an
ordinary node carries one: `definition_id` (repeating what the child file
declares), `instance_id` (an opaque `submodel_instance_<uuid>` or
`submodel_<n>` that leaks into runtime ids `submodel_runtime/<instance_id>/...`,
drilled-view targets and browser selectors) and the alias. An ordinary node's
id, function name and display name are one string; occurrences should be the
same.

**Plan:**

- The occurrence's node id is its name: the parser sets it, grouping mints it
  as the submodel name (it already mints the alias and the display name that
  way), Create Instance mints it with `nextSubmodelAlias`, and runtime ids
  become `submodel_runtime/<name>/<node>` on both sides
  (`qualified_runtime_node_id`, `submodelRuntimeTarget.ts`).
- The `instance_id` keyword disappears from `pipeline.submodel(...)`; the
  parser reads `definition_id` from the child file's own `Submodel(...)`
  declaration, so a registration is `pipeline.submodel(path, name)`. The
  definition's own `definition_id` stays the identity shared by its
  occurrences and never collapses into an occurrence name; a parent-side
  `instance_id=` or `definition_id=` keyword is a `ParseError` naming the fix.
- Renaming keeps the ordinary-node pattern: in the editor the display name and
  every consumer binding change while React Flow keeps the old id for the
  session; Save regenerates the registration and the reparse re-keys the id.
  The rename plan rewrites `instanceOf` on copies of a renamed owner as a
  schema-declared reference, exactly as it rewrites `data_input`-style
  references today.
- Frontend sweep: instance creation, the protected-definition-owner logic and
  the drilled-view runtime targets stop assuming opaque ids; browser selectors
  address occurrences by name.
- Accepted consequence, stated in the submodels specification: a rename
  changes the occurrence's identity, so caches, trace snapshots and drilled
  targets keyed on the old runtime id are invalidated, as they are when an
  ordinary node is renamed.

**Acceptance:** A registration is `pipeline.submodel(path, name)` and the
occurrence's node id, display name, `connect` name, consumer parameter name and
runtime id prefix are one string, proven by parser, codegen and flatten round
trips; a parent carrying `instance_id=` or `definition_id=` fails to parse
loud; renaming an owner rewrites its copies' `instanceOf` and keeps every
consumer executing (backend round trip and browser journey); the group,
dissolve, instance and rename journeys and the generated submodel-endpoint
family are green; no `submodel_instance_` or `submodel_<n>` id is minted
anywhere.

**Dependencies:** SUB-L02.

**Evidence:** `src/haute/_parser_submodels.py`; `src/haute/_submodel_instances.py`;
`src/haute/routes/_submodel_ops.py`; `frontend/src/hooks/useNodeHandlers.ts`;
`frontend/src/utils/submodelRuntimeTarget.ts`; `frontend/src/utils/nodeUpdatePlan.ts`;
`specs/submodels/high-level.md`.
