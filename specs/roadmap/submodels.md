# Submodels roadmap

## Scope

The authoring and identity model of submodel definitions and occurrences:
public ports, occurrence registration, and the names that connect, display
and execute them. Current behaviour is specified in
[the submodels specification](../submodels/low-level.md).

## Priorities

| Package | State | Priority | Outcome |
|---|---|---:|---|
| SUB-L03 | Planned | P2 | One identity per occurrence: the node id is the name, and a registration is `pipeline.submodel(path, name)`. |

## Planned improvements

`SUB-L03` is the remaining package. It follows F13
(`specs/roadmap/bug-findings-2026-09-05.md`), which made the parser bind every
Polars parameter by name with no inference, named an occurrence's outputs after
the occurrence, and bound a definition's port-fed nodes to the port name;
SUB-L01 (delivered 6 September 2026), which gave every public port exactly one
canonical name; and SUB-L02 (delivered 6 September 2026), which made an
occurrence's display name its alias and an editor rename an alias rename. The
principle is the one ordinary nodes and API-input frames already follow: a
thing has one name, that name is what you connect by, what the code reads, and
what the canvas shows; labels are not a second identity.

---

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

**Dependencies:** SUB-L02 (delivered).

**Evidence:** `src/haute/_parser_submodels.py`; `src/haute/_submodel_instances.py`;
`src/haute/routes/_submodel_ops.py`; `frontend/src/hooks/useNodeHandlers.ts`;
`frontend/src/utils/submodelRuntimeTarget.ts`; `frontend/src/utils/nodeUpdatePlan.ts`;
`specs/submodels/high-level.md`.
