# Submodels — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_submodel_instances.py` | Canonical reusable-instance resolver and validator, qualified-id expansion, schema-led reference rewriting, public-port binding, create-instance alias allocation, and targeted occurrence flattening. |
| `src/haute/_submodel_paths.py` | Validate route-level names, resolve recorded submodel references relative to the active pipeline directory, enforce project containment, and return typed malformed/outside-project errors plus the directory used as config base. |
| `src/haute/_pipeline_revision.py` | Build deterministic canonical-graph revisions and the separate raw-artifact editor-document revision used by recovery-aware compare-and-swap. |
| `src/haute/_flatten.py` | Public flatten/dissolve entry point: validates and expands canonical occurrences through `_submodel_instances.py`. |
| `src/haute/routes/_submodel_ops.py` | Pure (no I/O) graph transform: extract selected nodes out of a `PipelineGraph` into a new submodel, producing the updated parent graph and submodel metadata. |
| `src/haute/routes/submodel.py` | FastAPI router (`/api/submodel/*`): transform-only `POST /create` and `POST /dissolve`, plus read-only persisted `GET /{definition_id}`. It validates the current parent revision and maps failures without writing files. |

Related but external to this component:
- `src/haute/_parser_submodels.py` (expression-parsing) — parses
  `pipeline.submodel(...)` calls and submodel `.py` files, and calls into
  canonical reusable-instance helpers to build the hierarchical view at parse
  time. Parsed child graphs retain their declared description,
  preamble, and column-zero preserved blocks. It rejects nested references
  and duplicate declared submodel names before invoking the graph helpers, so
  this component never receives a deliberately truncated hierarchy.
- `src/haute/routes/_save_pipeline.py::SavePipelineService` (server-api) —
  owns the sole persistence boundary. Explicit Save derives added and removed
  definition files from the persisted-versus-submitted graph and applies
  no-clobber, ownership, reference-audit, and rollback rules there.

## Key types and data structures

### Reusable-instance contract (normative)

The canonical graph model separates definition-owned state from
occurrence-owned state:

```text
SubmodelEndpoint { nodeId: NodeId, handleId: HandleId | null }
SubmodelInputPort { name: str,
                    targets: ordered list[SubmodelEndpoint] }
SubmodelOutputPort { name: str,
                     source: SubmodelEndpoint }
SubmodelDefinition { definitionId, file, graph,
                     inputPorts[], outputPorts[], ...metadata }
SubmodelInstanceConfig { definitionId, alias, instanceOf?: InstanceId }
PipelineGraph { nodes[], edges[], submodels: map[definitionId, definition] }
```

`PipelineGraph` is consumed through typed attributes and validated model-copy
operations only. It deliberately has no legacy dictionary `[]`, `get`, or
in-place `update` surface.

An empty `SubmodelInputPort.targets` list represents a declared public frame
that has not yet been routed inside the definition. The declaration remains
serialisable when unbound. When an occurrence binds that port, the canonical
Save validation, hierarchical codegen, and execution expansion all raise a
contextual `ParseError` instead of silently dropping the incoming edge or
emitting a `connect` call that binds nothing; the normal parent-first flow
routes it in the drilled view before Save. `POST /api/pipeline/save` reports
that rejection, and every other structural save-time `ParseError`, as a 400
carrying the error's own context, never as an opaque 500.

Each `SUBMODEL` node is one occurrence and its node id is its name (`node.id == label == alias == name`).
Its config is validated as `SubmodelInstanceConfig`; an occurrence's
display name is its alias (`node.data.label == config.alias` is enforced on
validation, raising `ParseError` on mismatch), and its position remains an
ordinary mutable node field. No parallel instance registry
is permitted. For each definition exactly one occurrence has no `instanceOf`
and owns definition editing. Every other occurrence has a non-empty
`instanceOf` that points directly to that owner. The resolver rejects owner
chains, self references, missing or cross-definition owners, and multiple or
missing owners. Definitions are deduplicated by canonical resolved file during
parse, while explicit persisted ids remain authoritative and conflicting ids
for one file are rejected.

Definition node positions are local to the occurrence origin. Grouping subtracts
the new occurrence position from each selected node; expansion adds the target
occurrence position back. This makes one shared layout reusable at any number of
independent parent-canvas positions without coordinate drift.

Public handles are constructed only as `in__<name>` and `out__<name>`.
Port names are canonical identifiers; internal node ids and labels are not public
port names. Inputs fan out to their ordered targets, outputs have exactly one
source, port names are unique across both input and output ports in the definition, and every endpoint must
refer to a node in the definition graph.

The source form persists identity on both sides of the relationship:

```python
pipeline.submodel(
    "modules/scoring.py",
    "scoring_primary",
)

pipeline.submodel(
    "modules/scoring.py",
    "scoring_secondary",
    instance_of="scoring_primary",
)
```

Generated and authored definition source must persist its `definition_id` and
structured public port declarations. Every parent registration must persist
its path and occurrence name, plus `instance_of` for every
read-only instance. Missing identity is a parse error;
the parser never derives it. Duplicate aliases in one parent, conflicting
definition ids for one resolved path, and one definition id resolving to
different files are parse errors.

Expansion is a pure transform per instance:

1. Resolve `config.definitionId` and validate the complete definition and every
   parent binding before removing an occurrence.
2. Clone definition nodes and edges with qualified ids derived from the
   immutable instance id plus local id; record reversible origin metadata.
3. Build one schema-declared reference map per occurrence from local node ids
   to qualified runtime ids and from each bound public input port id to its
   upstream parent identity. Rewrite cloned child configs through that map.
   Also rewrite remaining parent consumers from the selected occurrence's
   authored alias (or <alias>__<port_id> when multi-output) to the qualified runtime output source. When
   this changes the physical name of an ordinary Polars input, preserve the
   public logical name with `inputMapping`. An unbound, ambiguous, or otherwise stale declared reference
   is an error. Unregistered opaque fields are unchanged, never guessed.
4. Expand each input binding to the port's ordered targets and each output
   binding from the port's single source, preserving authored endpoint handles
   and regenerating deterministic edge ids.
5. Assert that no occurrence or parent-to-internal-child endpoint remains and
   deduplicate only truly identical expanded edges.

Create-instance and remove-instance are I/O-free graph operations. Creation
normalises the selected source to its owner and persists that id in
`instanceOf`. Dissolve is addressed only by immutable `instance_id` and expands
exactly that occurrence. Dissolving an owner while any unselected instance
references it is rejected; instances must be dissolved first.
Removing the last occurrence does not by itself delete the
definition file; the existing managed ownership and project-wide reference
audit remains the sole deletion gate.
Definition edits validate all live occurrences and their bindings as one
transaction. Interface-breaking edits are rejected before any parent or child
file is written and report every affected instance/port.
The owner's drilled Input inspector is the explicit interface-retirement path.
Removing one listed frame deletes its `SubmodelInputPort`, every synthetic child route for that port, and
every `in__<name>` parent binding targeting an occurrence of the definition
in one history transaction. This operation is intentionally distinct from
ordinary boundary-edge deletion, which retains the shared in-use guard.
Input boundary history projections retain that definition-wide parent-binding
slice, and the parent edge order it was projected from, so Undo and Redo
restore the interface, its parent connections, and their original positions
together. Parent edge order is persisted state — the dirty fingerprint
compares it and codegen emits `connect` calls in it — so a restored binding
returns to its own position rather than to the end of the parent edge list.

Required regression coverage is added before implementation and includes:

- two instances of one definition with different positions and bindings;
- definition parse-once and file emit-once behavior;
- stable parse/codegen/parse ids and aliases;
- collision-free qualified runtime ids and reversible origins;
- schema-led rewrites for every declared node-id-bearing config field;
- invalid definition, port, endpoint, alias, and stale-reference failures;
- dissolve-one/remove-one preserving the sibling occurrence and definition;
  interface-breaking edit preflight.
- frontend create-instance identity persistence, editable-owner/shared-edit
  warning, read-only instance mutation guards, public-handle rendering, and
  interface-breaking edit preflight;
- explicit Input-inspector removal of routed and unrouted public inputs,
  including all-occurrence parent-binding cleanup, one atomic undo snapshot,
  and parent edge order preserved across that undo;
- codegen and save-route rejection of a parent edge bound to an unrouted
  public input, including the 400 status and message the client receives.

### Route request and response models

Pydantic route models live in `src/haute/schemas.py`. `CreateSubmodelRequest`
carries `name`, `node_ids`, the parent graph and support code, `source_file`,
`base_revision`, and pipeline metadata; `CreateSubmodelResponse` returns both
file paths, the revised graph, and the unchanged persisted `source_revision`.
`DissolveSubmodelRequest`
requires one non-empty, unpadded `instance_id` plus the same parent-document
fields. `DissolveSubmodelResponse` returns the resolved `instance_id` and
`definition_id`, the revised graph, and unchanged persisted revision. It
contains no child-file lifecycle fields and forbids extra fields so obsolete
internal producers fail loudly. `SubmodelGraphResponse` requires
`definition_id` and returns the child display name, file, and graph.
## Control flow

### `resolve_submodel_reference(rel_path, *, pipeline_dir, project_root)`

1. Normalise path separators and reject an empty or NUL-containing reference,
   or any explicit `..` traversal component, with
   `MalformedSubmodelPathError`.
2. `resolved_root = project_root.resolve()`; `active_dir = (pipeline_dir or
   project_root).resolve()`.
3. Build `submodel_path = (active_dir / rel_path).resolve()`.
4. If `submodel_path` is not below `resolved_root`, raise
   `SubmodelPathOutsideProjectError`.
5. Return `(submodel_path, active_dir)`.

### `validate_submodel_definition_id(definition_id)`

Reject an empty, NUL-containing, slash-containing, or backslash-containing
route definition id with `MalformedSubmodelPathError`. The validated id is
used only as an exact key into the parsed parent's definition registry; it is
never converted into a conventional `modules/<name>.py` path.

### `pipeline_document_revision(graph, *, pipeline_path, project_root)`

Build a versioned canonical payload from `graph.model_dump()` excluding
`source_revision` plus the relative path and content hash (or an explicit
missing marker) for the parent source/sidecar and every resolved child
source/sidecar. Canonical-JSON encode that payload and hash the bytes. Because
the parsed graph includes resolved node config content and sidecar positions,
dependency changes alter the revision even when the parent source text does
not. `parse_pipeline_to_graph` attaches this revision to every live graph
response and WebSocket refresh; a successful save reparses the committed
document and returns the new revision.

### `flatten_graph(graph, *, target_instance_id=None)`

1. Resolve and validate the complete definition registry, every `SUBMODEL`
   occurrence, unique alias, parent edge endpoint, public-port handle, and the
   at-most-one parent binding invariant for each input port before constructing
   output. `target_instance_id` selects exactly one occurrence; no target
   selects all.
2. For each selected canonical occurrence, clone definition nodes using
   delimiter-safe ids derived from `(instanceId, localNodeId)` and add the
   occurrence position to each definition-local position. Do not attach
   composition metadata to `NodeData.config`; executor builders validate that
   mapping as the node type's domain config.
3. Rewrite only schema-declared node-id config fields through the local-to-runtime
   id map. A stale declared reference is a `ParseError`; opaque fields are
   unchanged.
4. Clone internal edges with qualified endpoints. Expand each incoming parent
   binding to the selected input port's ordered targets and each outgoing
   binding from the selected output port's one source, preserving authored
   endpoint handles and all hidden-port components in deterministic edge ids.
   Rewrite every schema-owned incoming-frame reference from the public boundary
   name to the expanded physical name: exact selector fields,
   `input_scenario_map` keys, instance `inputMapping` values, and every OUTPUT
   `outputMapping[].source_port`. A malformed referenced mapping fails with
   contextual `ParseError`; it is never retained as a stale logical name.
   A rename that collides on a target's input names or on an OUTPUT mapping
   fails with `ParseError`; malformed mapping shapes are validated on renamed
   nodes. Two distinct old input names for one target mapping to one new name
   is a collision, as is a rewritten `outputMapping` in which two active
   entries become identical in `(source_port, source_column, output_path)` or
   two entries share `output_path` and `source_port` with different
   `source_column`.
5. Remove only selected occurrence nodes and their incident boundary edges.
   Deduplicate exact six-field edge identities, assert that no selected
   occurrence endpoint remains, and merge definition support code once. A
   definition preamble is appended only when its stripped line block is not
   already contained (whole-line, contiguous) in the merged parent preamble —
   exact-blob identity would re-append after a staged dissolve, and substring
   matching would wrongly swallow `import a` when `import ab` is present.
   Preserved blocks deduplicate by exact stripped identity per block.
6. Retain every unselected occurrence and every definition it still references;
   set `submodels=None` only when no definition remains. The transform is pure.

### `create_submodel_graph(graph, node_ids, name)`

1. Trim and sanitise `name`; reject blank names, repeated/stale selections,
   fewer than two children, or any selected `SUBMODEL` node without mutating
   the input graph.
2. Derive `definitionId = alias = sm_name` and
   `modules/<sm_name>.py`, and set the occurrence id to `sm_name`.
   Reject definition-id, alias, remaining-parent-node-id, and
   case-insensitive file collisions.
3. Partition edges into internal, cross-boundary, and external sets while
   preserving graph order.
4. Build structured public ports. Incoming edges sharing one external logical
   frame become one input port with ordered internal targets and exactly one
   parent binding. Outgoing edges sharing one internal source endpoint become
   one output port. Each port's name is the executable input name the boundary
   edge carried before grouping (`edge_input_name`), so child and parent
   consumer code requires no generated rename; a name already minted for the
   new definition, in either direction, gets the suffix `_2`, `_3`, ... A
   boundary edge whose source has no executable identity refuses creation with
   `SubmodelValidationError(code="invalid_input_binding", status_code=400)`.
5. The port models validate every name as a canonical identifier
   (`_sanitize_func_name(name) == name`) and the definition rejects a name used
   twice across both directions, so the graph is never built with an
   ambiguous interface. Codegen still rejects a duplicate derived input name at
   save.
6. Compute the selected bounding-box centre as the occurrence position and
   subtract it from every selected child position before storing the definition
   graph. Internal positions are therefore occurrence-local.
7. Create one typed `SubmodelDefinition` and one `SUBMODEL` occurrence whose
   config is exactly `{definitionId, alias}`. Rewire parent edges only through
   `in__<name>`/`out__<name>` handles, preserving still-hidden authored
   ports in both edge data and deterministic ids. Remaining parent consumers
   keep the input name they were authored with: the occurrence's name (or
   `<alias>__<name>` with several output ports) becomes the physical input
   and the previous name is recorded as the logical name through
   `inputMapping`, with schema-owned selectors rewritten, exactly as
   flattening does across the same boundary (F13).
8. Return a new parent graph with the prior registry entries preserved plus the
   definition and occurrence. Return `SubmodelGraphResult` metadata for the
   transform-only route; the input graph is untouched.

### `POST /api/submodel/create` (`create_submodel`)

Acquires `save_lock`, runs the body in a threadpool:
1. FastAPI rejects a missing, empty, or whitespace-only `base_revision` with its standard `422`
   request-validation envelope. Require a non-blank `source_file` in the
   route, resolve the parent safely, load its current ready editor document,
   and compare the raw-artifact `source_revision` retained by the editor. The
   route then parses a canonical graph only for the transform. A mismatch raises
   `HTTPException(409, <reload message>)` before any graph transform.
2. Compute `sm_filename = f"{_sanitize_func_name(body.name.strip())}.py"`; if
   `is_windows_reserved_filename(sm_filename)`, raise `HTTPException(400,
   <specific message>)` before touching `create_submodel_graph` at all.
3. Copy `body.preamble` and `body.preserved_blocks` onto the submitted graph,
   then call `create_submodel_graph`. `SubmodelValidationError` maps its
   stable safe detail to `400` or `409`; unexpected `ValueError` remains
   sanitised and logged.
4. Run a read-only source/sidecar no-clobber preflight for `result.sm_file`.
5. Return `CreateSubmodelResponse(status="ok", submodel_file=result.sm_file,
   parent_file=body.source_file, source_revision=current_graph.source_revision,
   graph=result.graph with that same revision)`. No file changes until Save.

### `GET /api/submodel/{definition_id}?source_file=<parent>` (`get_submodel` -> `_get_submodel_blocking`)

Acquires `save_lock` and runs in a threadpool:
1. Validate the exact `definition_id` and required `source_file` before
   parsing; map `MalformedSubmodelPathError` to `400`.
2. Resolve and parse exactly `source_file`. If its typed `submodels` registry
   has no exact `definition_id` key, return `404`; no other pipeline,
   display name, alias, or conventional path is consulted.
3. Resolve that definition's non-empty `file` relative to the parent file.
   `SubmodelPathOutsideProjectError` maps to `403`.
4. Require the recorded path to be a file, otherwise return `404` for that
   definition id.
5. Parse the child and apply sidecar positions while retaining parser
   positions for nodes without a sidecar entry.
6. Require the parsed child's declared `definition_id` to equal the route
   identity; a mismatch returns `409`.
7. Return `SubmodelGraphResponse` with `definition_id`, display
   `submodel_name`, recorded file, and graph.

### `POST /api/submodel/dissolve` (`dissolve_submodel`)

Acquires `save_lock` and runs the body in a threadpool:

1. Request validation requires a current revision, source file, and one
   non-empty unpadded `instance_id`.
2. Load the current ready editor document only to compare its raw-artifact
   revision with `base_revision`, then parse the current parent strictly for
   canonical validation. The submitted graph is the editor state being transformed,
   including unsaved newly created definitions.
3. Resolve the selected definition from the submitted graph, copy submitted
   support code, and call
   `flatten_graph(..., target_instance_id=instance_id)`. Only the selected
   occurrence is expanded.
4. Keep the definition whenever another occurrence references it.
5. Return `DissolveSubmodelResponse(status="ok", instance_id,
   definition_id, source_revision=current_graph.source_revision, graph=flat)`.
   The response contains no deletion boolean or retained-file field. Do not
   read, write, or delete any child artifact.

## Edge cases and invariants

- **Duplicate/nonexistent node ids in `node_ids` fail atomically.** Duplicate
  ids return a safe `400` and any unknown id returns `409`; neither case
  extracts the valid subset.
- **Nesting check runs before the count check.** A request selecting exactly
  one submodel occurrence node gets the nesting error, not "at least 2
  nodes" — verified explicitly by
  `test_submodel_ops.py::test_single_submodel_node_raises_nesting_not_count`.
- **One internal endpoint may participate in both public directions** through
  distinct typed ports; the immutable port ids, not the child id, distinguish
  the bindings.
- **Flatten validates before dropping anything.** Every occurrence must resolve
  a definition, every boundary handle must name a declared port with the right
  direction, every bound input port must have at least one internal target, and
  every public endpoint must exist. Missing, wrong-prefixed, unrouted, or stale
  bindings raise contextual `ParseError` before output construction.
- **Flatten is identity-preserving when the graph has no occurrences.** An
  explicit unknown `target_instance_id` is an error rather than a no-op.
- **Inbound edge-join roles survive flattening.** A public input port targeting
  an edge-join endpoint restores its authored base/join `targetHandle` and
  rewrites the port-id role reference to the bound upstream parent identity.
- **Outbound edge-join roles survive extraction and flattening.** A remaining
  edge join fed by one or more selected sources uses the occurrence alias (or
  <alias>__<port_id> when multi-output) while hierarchical, then qualified
  runtime source ids after expansion; two outputs of one occurrence never
  collapse to the shared occurrence id.
- **`_submodel_paths.py` checks the resolved pipeline-relative path before
  returning it** — a relative reference that escapes the project fails closed.
- **The Windows reserved-name check is platform-unconditional.** It runs on
  every OS at create time (not just Windows), so a pipeline saved on
  Linux/macOS cannot mint a submodel that becomes unloadable on a Windows
  checkout.
- **Persisted drill-down applies `_apply_sidecar_positions`; dissolve does not
  re-read disk.** Dissolve expands the submitted canonical definition so
  unsaved definition edits behave like ordinary graph state.
- **Disconnected selections are supported.** Neither graph connectivity nor a
  cross-boundary edge is required; an all-node selection is valid.
- **Child id lists and occurrence positions are deterministic.** Ids follow
  parent node order and the occurrence uses the selected bounding-box centre.
- **Ownership never comes from a transform request.** Explicit Save alone may
  claim a new child after source and sibling sidecar pass no-clobber. Explicit
  Save alone may delete a removed child and still requires its marker plus a
  complete reference audit finding no other parent.
- **Persisted identity cannot be substituted in place.** Explicit Save compares
  definition ids for every canonical child path present in both the persisted
  and submitted registries. A different id for the same path is a `409`, not
  an unchanged definition or an implicit migration.

## Error handling

| Situation | Exception / status | Where it surfaces |
|---|---|---|
| Blank name, duplicate selection, too few nodes, or nesting | `SubmodelValidationError` → `HTTPException(400, <safe detail>)` | `routes/submodel.py::create_submodel`. |
| Unknown selected id, existing canonical submodel, or stale `base_revision` | `SubmodelValidationError`/revision mismatch → `HTTPException(409, <safe detail>)` | Before transform. |
| Existing case-insensitive target module or sibling sidecar | `HTTPException(409, <safe detail>)` | Create read-only preflight and explicit Save authoritative preflight. |
| Submitted definition replaces the persisted definition id for the same canonical child path | `HTTPException(409, <identity detail>)` | `SavePipelineService` lifecycle diff, before writes. |
| Submitted dissolve definition is malformed | Request validation or `ParseError` mapped to `400` | Before the pure flatten transform. |
| Submodel name collides with a Windows reserved device filename | `HTTPException(400, <specific message>)` | `create_submodel`, before `create_submodel_graph` runs. |
| Missing/blank `base_revision` on create/dissolve | FastAPI request validation → `422` | Before the route runs. |
| Missing/blank `source_file` on create/dissolve | `HTTPException(400, <specific message>)` | Before disk access. |
| Dissolve `instance_id` does not identify a canonical occurrence in the submitted graph | `HTTPException(404, <instance detail>)` | `dissolve_submodel`. |
| Drill-down parent does not contain the exact definition id, or its recorded `.py` is missing | `HTTPException(404, <definition detail>)` | `_get_submodel_blocking`. |
| Empty/NUL-containing reference, explicit `..` reference component, or route name containing `/` or `\` | `MalformedSubmodelPathError` → `HTTPException(400)` | `_submodel_paths.py`, mapped by drill-down/dissolve. |
| Recorded reference resolves outside project root | `SubmodelPathOutsideProjectError` → `HTTPException(403)` | `_submodel_paths.py`, mapped by drill-down/dissolve. |
| Null handle on an inbound edge targeting a selected submodel | `ParseError` with definition/instance/edge context | `flatten_graph` raises through `resolve_submodel_instances` (matching `_port_name`); the transform returns no graph and touches no files. |
| Missing or wrong-prefixed public handle, undeclared port id, or invalid definition endpoint passed to `flatten_graph` | `ParseError` with definition/instance/edge context | `_submodel_instances` validation; the transform returns no graph and touches no files. |
| Sanitised node-name collision | `HTTPException(400, <specific collision detail>)` | `SavePipelineService` validation, before writes. |
| Any write step in the later explicit Save transaction fails (config write, sidecar write, module delete) | Best-effort rollback by `SavePipelineService`, original error re-raised | The server's generic exception middleware produces `500 {"detail": "Internal server error"}`; a failed compensating operation is logged and can leave partial state. See [server-api](../server-api/high-level.md). |
| Child is hand-authored/shared, ownership is ambiguous, or reference audit is incomplete | Later explicit Save retains the source and sidecar | Uncertainty never authorises deletion. |

## Testing

Tests live in `tests/test_submodel_identity.py`, `tests/test_submodel_instances.py`, `tests/test_submodel_ops.py`,
`tests/test_submodel_routes.py`, `tests/test_submodel_route_contracts.py`,
`tests/test_submodel_outport_invariant.py`,
`tests/test_submodel.py`, `tests/test_edge_join.py`, `tests/test_flatten.py`,
`tests/test_flattening_dedup.py`, `tests/test_pipeline_revision.py`, and
`tests/test_submodel_persistence.py`, with related parser coverage in
`tests/test_parser_submodels.py`.

- `tests/test_submodel_identity.py` — the SUB-L03 contract: an owner and a copy
  parse to occurrences whose node id, label and alias are the name and whose
  `definitionId` is the child file's declaration; `definition_id=`,
  `instance_id=` and `alias=` keywords, a missing name and a non-canonical
  name fail to parse with the fix named; codegen emits
  `pipeline.submodel(path, name[, instance_of=owner])` and round-trips
  byte-identically; a stale editor node id never reaches generated code;
  runtime ids read `submodel_runtime/<name>/<child>`; grouping mints the name
  as the node id; sidecar positions are keyed by the alias.
- `tests/test_submodel_ops.py` — unit tests of `create_submodel_graph` against
  hand-built graphs (via `tests/conftest.py::make_graph`): basic extraction,
  structured input/output port construction and parent boundary rewiring,
  canonical definition/occurrence metadata, copied preamble/preserved blocks,
  and preservation of
  pre-existing submodel entries when adding a new one, name sanitisation
  (`_sanitize_func_name`, case-preserving), strict blank/duplicate/unknown-id
  errors, too-few-node and nesting errors, graph-order child ids,
  bounding-box-centred occurrence placement, selecting every node in
  the graph, multiple input/multiple output/bidirectional port scenarios, the
  no-cross-edges case, and all three nesting-rejection shapes (two submodel
  nodes selected, one submodel node selected, one submodel node mixed with a
  regular node).
- `tests/test_submodel_routes.py` — full FastAPI `TestClient` coverage of all
  three endpoints against an isolated `tmp_path` cwd: invalid selections and
  revision preconditions; read-only create no-clobber; transform-only
  create/dissolve without codegen, parent writes, sidecar writes, or child
  deletion; dissolve from an unsaved submitted definition; and persisted,
  pipeline-scoped drill-down/path failures.
- `tests/test_submodel_route_contracts.py` — focused end-to-end response and
  revision contracts, scoped persisted drill-down, create no-clobber, and the
  invariant that dissolve exposes no file-lifecycle compatibility state.
- `tests/test_submodel.py` — public flatten/codegen integration, including
  per-child source generation, child preamble and preserved-block round trips,
  compilable parent output, parsing, and request/response model contracts.
- `tests/test_edge_join.py` — create/codegen integration for multiple public
  outputs feeding distinct edge-join roles through `out__<name>` handles.
- `tests/test_submodel_port_names.py` — the SUB-L01 contract: port models carry
  exactly `name`, names must be canonical identifiers, a `portId` or `label`
  key fails to parse (and the DSL raises) with the fix named, codegen emits
  `name` only, the identity request and recovery document carry no label or
  identity-map field, and no port-id or label token survives in `src/haute`.
- `tests/test_submodel_occurrence_names.py` — the SUB-L02 contract: one name per
  submodel occurrence (the alias), `node.data.label == config.alias` is enforced on
  validation, non-canonical aliases are rejected at the DSL, parser, and config
  levels, `pipeline.submodel()` rejects `label=`, codegen emits `alias=` without
  `label=` and enforces the collision gate across occurrence aliases, recovery uses
  the alias only, and graph merge derives node label directly from the alias.
- `tests/test_submodel_instances.py` — canonical definition and occurrence
  validation, parse/codegen round trips, public-port expansion, targeted
  flattening, shared-definition retention, OUTPUT source-port migration across
  a public boundary, and explicit rejection of missing identity or malformed
  topology.
- `tests/test_flattening_dedup.py` — parity between parser-driven flattening
  and the shared `flatten_graph` implementation, including single-node,
  multi-node, chained, nested, and hierarchical-then-flat cases.
- `tests/test_pipeline_revision.py` — deterministic document-revision coverage,
  including self-field exclusion and invalidation by parent/child source,
  parent/child sidecar, and canonical graph-config changes.
- `tests/test_submodel_persistence.py` — the explicit Save transaction's
  submodel-specific filesystem contract: derived new-definition ownership,
  case-insensitive no-clobber, managed child position sidecars, rejection of
  hand-authored/orphan-sidecar clobber and in-place definition-identity
  replacement, safe source-plus-sidecar deletion with rollback, and retention
  when another pipeline references the child or the project-wide audit is
  incomplete.
- `tests/test_parser_submodels.py` — expression parsing, recursive loading,
  canonical child metadata, cross-boundary port reconstruction, and
  hierarchical/flat parser behaviour.

Canonical-only identities are pinned throughout these suites: submodel paths
are project-relative, boundary edge ids include port identity, and recorded
sidecar paths take precedence over convention-based lookup.
