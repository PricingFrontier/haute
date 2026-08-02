# Submodels — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_submodel_graph.py` | Shared helpers: build a submodel placeholder node, classify cross-boundary edges into input/output ports, rewire edges to/from a placeholder. Used by both the parser's hierarchical merge and the GUI create-submodel operation. |
| `src/haute/_submodel_paths.py` | Validate route-level names, resolve recorded submodel references relative to the active pipeline directory, enforce project containment, and return typed malformed/outside-project errors plus the directory used as config base. |
| `src/haute/_pipeline_revision.py` | Build deterministic live-document revisions from canonical parsed graph state plus parent/child source and sidecar content hashes. |
| `src/haute/_flatten.py` | Dissolve one named submodel or every submodel into a flat graph: omit deliberate null-handle inbound drafts, validate and consume mapped boundary handles, inline stored child nodes/internal edges, restore authored and edge-join ports, regenerate port-complete edge ids, merge child preamble/preserved blocks, deduplicate exact edges, and retain metadata for untargeted submodels. |
| `src/haute/routes/_submodel_ops.py` | Pure (no I/O) graph transform: extract selected nodes out of a `PipelineGraph` into a new submodel, producing the updated parent graph and submodel metadata. |
| `src/haute/routes/submodel.py` | FastAPI router (`/api/submodel/*`): `POST /create`, `GET /{name}`, `POST /dissolve`. Resolves authoritative read paths, maps domain/path failures to HTTP responses, and delegates every write/delete to the shared save transaction. |

Related but external to this component:
- `src/haute/_parser_submodels.py` (expression-parsing) — parses
  `pipeline.submodel(...)` calls and submodel `.py` files, and calls into
  `src/haute/_submodel_graph.py`'s same three helpers to build the hierarchical view at
  parse time. Parsed child graphs retain their declared description,
  preamble, and column-zero preserved blocks. It rejects nested references
  and duplicate declared submodel names before invoking the graph helpers, so
  this component never receives a deliberately truncated hierarchy.
- `src/haute/routes/_save_pipeline.py::SavePipelineService` (server-api) —
  provides `save_graph_transactionally`, reused by both create and dissolve.

## Key types and data structures

- `SubmodelGraphResult` (dataclass, `_submodel_ops.py`) — `graph:
  PipelineGraph` (the updated parent graph), `sm_file: str`
  (`modules/<name>.py`), `sm_name: str` (sanitised), `child_node_ids:
  list[str]`.
- `PipelineGraph.submodels: dict[str, Any] | None` (`_types.py`) — keyed by
  submodel name, each entry shaped `{"file": str, "childNodeIds": list[str],
  "inputPorts": list[str], "outputPorts": list[str], "managed": bool,
  "graph": dict}` where `outputPorts` is an ordered public-interface
  declaration and is not inferred solely from current consumers;
  `"graph"` is the submodel's own canonical `PipelineGraph` dump:
  `nodes`, `edges`, `pipeline_name`, `pipeline_description`, `preamble`,
  `preserved_blocks`, and `source_file`.
- `NodeType.SUBMODEL = "submodel"` / `NodeType.SUBMODEL_PORT = "submodelPort"`
  (`_types.py`) — the placeholder node's type; `SUBMODEL_PORT` is used only by
  the drill-down GUI view, not produced by anything in this component.
- Placeholder node shape (`build_submodel_placeholder`): `id =
  f"submodel__{sm_name}"`, caller-supplied `position` (defaulting to
  `{"x": 0, "y": 0}` for parser reconstruction),
  `data.config = {"file", "childNodeIds", "inputPorts", "outputPorts",
  "outputPortLabels"}`. `outputPortLabels` maps each output child id to the
  authoritative child node label; ids remain the stable edge identity and
  labels are presentation only. The mapping is optional at frontend read time
  for compatibility with older payloads, which display the child id.
- Boundary edge handles: `targetHandle = f"in__{child_id}"` on an edge whose
  target is the placeholder; `sourceHandle = f"out__{child_id}"` on an edge
  whose source is the placeholder. These are the only two synthetic handle
  shapes this component produces or consumes. When one of those synthetic
  handles replaces an authored connect port, `GraphEdge.targetPort` or
  `GraphEdge.sourcePort` carries the hidden authored value until codegen or
  flattening restores it; unset supplemental fields are omitted from payloads.
- Pydantic request/response models (`src/haute/schemas.py`, owned by server-api):
  `CreateSubmodelRequest`
  (`name`, `node_ids: list[str]`, `graph: Graph`, `preamble`, `source_file`,
  `preserved_blocks`, `base_revision`, `pipeline_name = "main"`,
  `pipeline_description`), `CreateSubmodelResponse` (`status`,
  `submodel_file`, `parent_file`, `source_revision`, `graph`),
  `DissolveSubmodelRequest` (`submodel_name`, `graph`, `preamble`,
  `preserved_blocks`, `source_file`, `base_revision`, `pipeline_name`,
  `pipeline_description`), `DissolveSubmodelResponse` (`status`, `graph`,
  `source_revision`, `submodel_file_deleted`, `retained_submodel_file`),
  `SubmodelGraphResponse` (`status`, `submodel_name`, `submodel_file`,
  `graph`).
- `PipelineGraph.source_revision` is response metadata computed from the
  persisted parent graph plus its source/sidecar and referenced child
  source/sidecar states. It is excluded from its own digest.
- Managed child sidecars extend `SidecarModel` with optional
  `managed_parent: str`, the canonical project-relative parent source path.
  Absence never implies ownership.

## Control flow

### `build_submodel_placeholder(sm_name, sm_file, child_node_ids, input_ports, output_ports, *, output_port_labels=None, description="", position=None)`

Pure construction of one `GraphNode`. No validation of its inputs beyond
what the type constructors enforce. `output_port_labels` is filtered to the
declared `output_ports` and emitted in that port order so config never exposes
a label for a non-exported child. Create derives it from the selected child
nodes and hierarchical parser merge derives it from the parsed child graph.

### `classify_ports(cross_edges, child_node_ids)`

Iterates canonical `GraphEdge` instances (reading `edge.source` and
`edge.target`) and buckets each
into `input_ports` (target inside, source outside) and/or `output_ports`
(source inside, target outside) — a single node can land in both lists if it
has both directions of cross-boundary edge. Both lists are order-preserving
and deduplicated by first occurrence.

### `rewire_edges(edges, sm_node_id, child_node_ids)`

For each edge, classify `src_inside`/`tgt_inside` against `child_node_ids`:
- both inside → dropped (internal edge, lives inside the submodel file).
- target inside only → target becomes `sm_node_id`, `targetHandle =
  f"in__{e.target}"`, **`sourceHandle` is preserved from the original edge**
  (not cleared) so that a child-of-A → child-of-B edge, rewired once per
  submodel across two separate calls, keeps the boundary handle set by
  whichever pass ran first; the replaced authored target handle moves to
  `targetPort`.
- source inside only → source becomes `sm_node_id`, `sourceHandle =
  f"out__{e.source}"`, `targetHandle` preserved for the same reason, and the
  replaced authored source handle moves to `sourcePort`. The synthetic source
  handle remains the authoritative logical child identity for downstream
  consumers: in particular, edge-join role ordering resolves `out__<child_id>`
  before comparing the connection with `baseInput`/`joinInput`. The downstream
  role config continues to name the child, not the placeholder, so two outputs
  of one submodel remain distinct inputs.
- neither inside → passed through unchanged.

Every rewired boundary id appends a deterministic digest of the authored
source/target ports (including when both are absent), so edges sharing endpoints
but carrying distinct hidden handles remain unique. Cross-boundary
reconstruction likewise deduplicates only an exact endpoint-and-authored-port
identity through hierarchical merge and flattening.

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

`resolve_submodel_by_name(name, *, pipeline_dir, project_root)` is a thin
wrapper that first rejects empty, NUL-containing, slash-containing, or
backslash-containing route names with `MalformedSubmodelPathError`, then calls
the reference resolver with `rel_path=f"modules/{name}.py"`.

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

### `flatten_graph(graph, target_name=None)`

1. If `graph.submodels` is falsey, return the original graph object. With a target name, intersect
   it with the metadata keys; if there is no match, also return the original object.
2. Ask `build_edge_join_boundary_target_roles` for any target-port roles that must be restored on
   inbound edges to child edge-join nodes.
3. Pydantic-validate each selected embedded graph, index its child ids, copy
   the parent node/edge lists, remove selected placeholders, and append child
   nodes/internal edges.
4. Omit an edge targeting a selected placeholder when its `targetHandle` is
   exactly `None`: this is the explicit unassigned-input editor draft and has
   no executable child endpoint. For every other edge incident to a selected
   placeholder, require the appropriate non-empty `out__<child_id>` or
   `in__<child_id>` handle and verify that the child exists in that embedded
   graph. A mismatch raises `ParseError` with edge/submodel/known-child context
   before rewiring continues.
5. Replace each valid placeholder endpoint with the child id, restore the
   authored `sourceHandle`/`targetHandle` from hidden `sourcePort`/
   `targetPort` (or the edge-join role from step 2), clear the consumed hidden
   field, and regenerate the id with `_edge_id` over endpoints, rendered
   handles, and any hidden ports belonging to an untargeted boundary.
6. Deduplicate only exact six-field edge identities, preserving first
   occurrence. Merge selected child preambles into the root preamble with a
   blank-line separator only when the same trimmed preamble snapshot has not
   already been merged. Append selected child preserved blocks in order while
   skipping exact trimmed block duplicates.
7. Return `graph.model_copy(...)`, removing flattened metadata entries and
   setting `submodels=None` when none remain. Untargeted
   placeholders/metadata remain intact.

### `create_submodel_graph(graph, node_ids, name)`

1. Trim `name` and reject an empty result with
   `SubmodelValidationError(code="blank_name", status_code=400, ...)`.
   `sm_name = _sanitize_func_name(trimmed_name)` and `sm_file =
   f"modules/{sm_name}.py"`.
2. Reject repeated request ids with `code="duplicate_selection"` (`400`).
   Build `selected_ids` only after that check.
3. Compare `selected_ids` with the ids in `graph.nodes`. Any missing id raises
   `code="stale_selection"` (`409`); no requested id is discarded.
4. Build `child_nodes` and `parent_nodes` by iterating `graph.nodes` once, so
   `child_node_ids = [node.id for node in child_nodes]` retains canonical graph
   order. A separate `child_node_id_set` serves membership tests.
5. If any selected child is `NodeType.SUBMODEL`, raise
   `code="nested_submodel"` (`400`). If fewer than two children remain, raise
   `code="too_few_nodes"` (`400`). If `sm_name` already exists in
   `graph.submodels` or its placeholder id already exists under a
   case-insensitive comparison, raise `code="submodel_exists"` (`409`).
6. Classify `graph.edges` into `internal_edges` (both endpoints in
   `child_node_id_set`), `cross_edges` (exactly one endpoint inside — XOR), and
   `external_edges` (neither endpoint inside).
7. `classify_ports` on canonical `cross_edges` →
   `input_ports`, `output_ports`.
8. Build the submodel's canonical internal graph dict: `{"nodes": [child node
   dumps], "edges": [internal edge dumps], "pipeline_name": sm_name,
   "pipeline_description": "", "preamble": graph.preamble or "",
   "preserved_blocks": list(graph.preserved_blocks),
   "source_file": sm_file}`.
9. Compute the min/max centre of the selected nodes' positions and pass it to
   `build_submodel_placeholder(...)`; `rewire_edges(cross_edges, sm_node.id,
   child_node_id_set)` → `rewired_cross`.
10. Assemble the new parent graph: `nodes = parent_nodes + [sm_node]`, `edges =
   external_edges + rewired_cross`, `submodels = {**existing, sm_name: {...,
   "managed": True, "graph": sm_graph}}` — existing submodel entries are preserved by starting
   from `dict(graph.submodels or {})`.
11. Return `SubmodelGraphResult(graph=new_graph, sm_file=sm_file,
    sm_name=sm_name, child_node_ids=child_node_ids)`.

### `POST /api/submodel/create` (`create_submodel`)

Acquires `save_lock`, runs the body in a threadpool:
1. FastAPI rejects a missing, empty, or whitespace-only `base_revision` with its standard `422`
   request-validation envelope. Require a non-blank `source_file` in the
   route, resolve the parent safely, parse its current persisted document
   state, and compare its `source_revision`. A mismatch raises
   `HTTPException(409, <reload message>)` before any graph transform.
2. Compute `sm_filename = f"{_sanitize_func_name(body.name.strip())}.py"`; if
   `is_windows_reserved_filename(sm_filename)`, raise `HTTPException(400,
   <specific message>)` before touching `create_submodel_graph` at all.
3. Copy `body.preamble` and `body.preserved_blocks` onto the submitted graph,
   then call `create_submodel_graph`. `SubmodelValidationError` maps its
   stable safe detail to `400` or `409`; unexpected `ValueError` remains
   sanitised and logged.
4. `SavePipelineService(project_root=Path.cwd(),
   pipeline_root=pipeline_dir()).save_graph_transactionally(graph=result.graph,
   name=body.pipeline_name, description=body.pipeline_description or "",
   preamble=body.preamble, source_file=body.source_file,
   require_absent_module_files=[result.sm_file],
   claim_managed_module_files=[result.sm_file])` — this is where
   sanitized-name uniqueness is validated, `graph_to_code_multi` runs, and
   the parent source, new `modules/<name>.py`, parent sidecar, and managed
   child sidecar are written under the save transaction's no-clobber,
   allowlist, and rollback contract (see server-api).
5. Return `CreateSubmodelResponse(status="ok", submodel_file=result.sm_file,
   parent_file=body.source_file, source_revision=save.source_revision,
   graph=result.graph with that revision)`.

### `GET /api/submodel/{name}?source_file=<parent>` (`get_submodel` → `_get_submodel_blocking`)

Acquires `save_lock`, runs in a threadpool:
1. Validate `name` and the required `source_file` before parsing; map
   `MalformedSubmodelPathError` to `400`.
2. Resolve and parse exactly `source_file`. If its `submodels` metadata does
   not contain `name`, return `404`; no other pipeline or conventional path is
   consulted.
3. Resolve the selected metadata entry's non-empty `"file"` relative to that
   parent file's directory. `SubmodelPathOutsideProjectError` maps to `403`.
4. `sm_path.is_file()` → else `HTTPException(404, f"Submodel '{name}' not
   found")`.
5. `parse_submodel_file(sm_path, _base_dir=config_base)` → `sm_graph`.
6. `_apply_sidecar_positions(sm_graph, sm_path)` loads sidecar positions and
   applies each matching `(x, y)` via per-node `model_copy`. Parsed nodes
   without a sidecar entry retain their parser-provided position.
7. Return `SubmodelGraphResponse(status="ok", submodel_name=
   sm_graph.pipeline_name or name, submodel_file=recorded_path,
   graph=sm_graph)`.

### `POST /api/submodel/dissolve` (`dissolve_submodel`)

Acquires `save_lock`, runs the body in a threadpool:
1. FastAPI rejects a missing, empty, or whitespace-only `base_revision` with its standard `422`
   request-validation envelope. Require a non-blank `body.source_file` in the
   route, then resolve and parse that exact parent. Compare the current
   `source_revision` before trusting any submitted graph metadata. A mismatch
   returns `409`.
2. Look up `body.submodel_name` in both the current disk parent and
   `body.graph.submodels or {}` → `404` if absent. The disk entry supplies the
   authoritative `"file"` and ownership state; the submitted graph continues
   to carry intentional unsaved parent edits.
3. Resolve the recorded file relative to the resolved parent file's directory, mapping typed
   malformed/outside-project path errors to `400`/`403`, and return `404` if
   the file is absent.
4. `parse_submodel_file(sm_path, _base_dir=config_base)`, apply its sidecar
   positions through `_apply_sidecar_positions`, and replace only the
   selected metadata entry's `"graph"` with that authoritative disk graph.
   Copy `body.preamble` and `body.preserved_blocks` onto the parent graph
   before flattening.
5. `flatten_graph(authoritative_graph, target_name=sm_name)` → `flat`. A
   stale boundary referencing a child no longer present on disk raises
   `ParseError`; any other submodels remain placeholders. Child preamble and
   preserved blocks are now part of `flat`; exact preamble/block snapshots
   already present in the parent are not duplicated.
6. Resolve the child sidecar's `managed_parent` and compare it with the
   canonical parent path. Audit every other discovered pipeline by resolved
   child path. A matching owner plus a complete audit with no other reference
   schedules the child source and sidecar for deletion. Missing ownership,
   another reference, or any unparseable sibling retains both.
7. `SavePipelineService(...).save_graph_transactionally(graph=flat,
   preamble=flat.preamble, ..., delete_module_files=[sm_file] only when
   deletion is authorised)` validates the final graph and rewrites the parent
   through the same transaction.
8. Return `DissolveSubmodelResponse(status="ok", graph=flat,
   source_revision=save.source_revision, submodel_file_deleted=<bool>,
   retained_submodel_file=sm_file when retained else None)`.

## Edge cases and invariants

- **Duplicate/nonexistent node ids in `node_ids` fail atomically.** Duplicate
  ids return a safe `400` and any unknown id returns `409`; neither case
  extracts the valid subset.
- **Nesting check runs before the count check.** A request selecting exactly
  one submodel placeholder node gets the nesting error, not "at least 2
  nodes" — verified explicitly by
  `test_submodel_ops.py::test_single_submodel_node_raises_nesting_not_count`.
- **A child node can be both an input port and an output port** if it has
  both an inbound and an outbound cross-boundary edge (`classify_ports`
  appends to both lists independently).
- **Cross-submodel edge rewiring is order-independent for handles and edge
  identity.** `rewire_edges` is called once per
  submodel being processed; a `child-of-A → child-of-B` edge is rewired twice
  across two separate `rewire_edges` calls (once when A is processed, once
  when B is), and each pass must not clobber the opposite side's
  already-set handle to `None` — this is why `sourceHandle`/`targetHandle`
  are explicitly *preserved from the input edge*, not always cleared, on the
  side that isn't being rewired in that pass. Boundary ids include both
  authored ports, and targeted flatten regenerates ids from every visible and
  still-hidden port, so one-sided flattening cannot collapse distinct edges.
- **Flatten is identity-preserving on no-op calls.** With no submodel metadata, an empty metadata
  dict, or a `target_name` absent from the metadata, `flatten_graph` returns the exact input object
  (`is`, not merely equality).
- **Flatten rejects malformed boundary handles before dropping anything.**
  The exact exception is a `None` target handle on an edge entering a selected
  placeholder: that is an unassigned editor draft and is omitted because it
  has no executable child endpoint. Every mapped inbound edge and every
  outbound edge must use `in__`/`out__`, respectively, and name a child in the
  selected embedded graph. Wrong-prefixed, missing outbound, and stale-child
  handles raise contextual `ParseError` before any mapped edge is discarded.
  Exact six-field edges alone are deduplicated after valid rewiring.
- **Inbound edge-join roles survive flattening.** An `in__<child>` boundary targeting an
  edge-join child recovers the base/join `targetHandle` through
  `build_edge_join_boundary_target_roles`; ordinary inbound boundaries clear the synthetic handle
  to `None`.
- **`_submodel_paths.py` checks the resolved pipeline-relative path before
  returning it** — a relative reference that escapes the project fails closed.
- **The Windows reserved-name check is platform-unconditional.** It runs on
  every OS at create time (not just Windows), so a pipeline saved on
  Linux/macOS cannot mint a submodel that becomes unloadable on a Windows
  checkout.
- **Drill-down and dissolve share `_apply_sidecar_positions`.** Each matching
  node is individually `model_copy`'d; nodes without an entry keep their
  parser-provided position. Dissolve therefore combines authoritative source
  structure with authoritative canvas layout before flattening.
- **Disconnected selections are supported.** Neither graph connectivity nor a
  cross-boundary edge is required; an all-node selection is valid.
- **Child id lists and placeholder positions are deterministic.** Ids follow
  parent node order and the placeholder uses the selected bounding-box centre.
- **Ownership never comes from the request body alone.** A normal save that
  receives `managed=true` without a matching persisted marker fails `409`.
  Only create can claim a brand-new child, after both source and sibling
  sidecar pass no-clobber. Deletion still requires that persisted marker plus a
  complete reference audit finding no other parent.

## Error handling

| Situation | Exception / status | Where it surfaces |
|---|---|---|
| Blank name, duplicate selection, too few nodes, or nesting | `SubmodelValidationError` → `HTTPException(400, <safe detail>)` | `routes/submodel.py::create_submodel`. |
| Unknown selected id, existing canonical submodel, or stale `base_revision` | `SubmodelValidationError`/revision mismatch → `HTTPException(409, <safe detail>)` | Before transform/save. |
| Existing case-insensitive target module or sibling sidecar | `HTTPException(409, <safe detail>)` | `SavePipelineService` no-clobber preflight, before writes. |
| Request asserts managed ownership without a matching sidecar or create claim | `HTTPException(409, <reload/safety detail>)` | `SavePipelineService` ownership preflight, before writes. |
| Create claim matches no resolved child metadata path (e.g. parent pipeline nested below the pipeline root) | `HTTPException(400, <specific message>)` | `SavePipelineService` ownership preflight, before writes. |
| Submitted dissolve metadata for the selected submodel is not an object | `HTTPException(400, <specific message>)` | Before authoritative child metadata is merged or any write runs. |
| Submodel name collides with a Windows reserved device filename | `HTTPException(400, <specific message>)` | `create_submodel`, before `create_submodel_graph` runs. |
| Missing/blank `base_revision` on create/dissolve | FastAPI request validation → `422` | Before the route runs. |
| Missing/blank `source_file` on create/dissolve | `HTTPException(400, <specific message>)` | Before disk access. |
| Dissolve target not in `graph.submodels` | `HTTPException(404, f"Submodel '{sm_name}' not found in graph")` | `dissolve_submodel`. |
| Drill-down parent does not record the name or target `.py` is missing | `HTTPException(404, f"Submodel '{name}' not found")` | `_get_submodel_blocking`. |
| Empty/NUL-containing reference, explicit `..` reference component, or route name containing `/` or `\` | `MalformedSubmodelPathError` → `HTTPException(400)` | `_submodel_paths.py`, mapped by drill-down/dissolve. |
| Recorded reference resolves outside project root | `SubmodelPathOutsideProjectError` → `HTTPException(403)` | `_submodel_paths.py`, mapped by drill-down/dissolve. |
| Null handle on an inbound edge targeting a selected submodel | Edge omitted as an unassigned editor draft | `flatten_graph`; preview/trace continue without inventing a child mapping, and dissolve removes the draft with the placeholder. |
| Missing outbound handle, wrong-prefixed mapped handle, or unknown child passed to `flatten_graph` | `ParseError` with edge/submodel context | `_flatten._boundary_child_id`; dissolve stops before save/delete. |
| Sanitised node-name collision | `HTTPException(400, <specific collision detail>)` | `SavePipelineService` validation, before writes. |
| Any write step in the underlying save transaction fails (config write, sidecar write, module delete) | Best-effort rollback by `SavePipelineService`, original error re-raised | The server's generic exception middleware produces `500 {"detail": "Internal server error"}`; a failed compensating operation is logged and can leave partial state. See [server-api](../server-api/high-level.md). |
| Child is hand-authored/shared, ownership is ambiguous, or reference audit is incomplete | Successful dissolve with `submodel_file_deleted=false` and `retained_submodel_file` | Source and sidecar are not scheduled for deletion. |

## Testing

Tests live in `tests/test_submodel_graph.py`, `tests/test_submodel_ops.py`,
`tests/test_submodel_routes.py`, `tests/test_submodel_route_contracts.py`,
`tests/test_submodel_outport_invariant.py`,
`tests/test_submodel.py`, `tests/test_edge_join.py`, `tests/test_flatten.py`,
`tests/test_flattening_dedup.py`, `tests/test_pipeline_revision.py`, and
`tests/test_submodel_persistence.py`, with related parser coverage in
`tests/test_parser_submodels.py`.

- `tests/test_submodel_graph.py` — pure unit tests of the three
  `_submodel_graph.py` functions in isolation: placeholder construction
  (id/type/config shape, description passthrough including special
  characters, empty ports, empty child list); `classify_ports` (basic
  classification, dedup with order preservation, no-cross-edges, a node
  being both input and output, all-internal edges producing no ports);
  `rewire_edges` (internal drop, external passthrough, inbound/outbound
  rewire, mixed edge sets, deterministic edge ids, multiple inbound/outbound
  edges to/from the same child, and the cross-submodel two-pass handle
  preservation case).
- `tests/test_submodel_ops.py` — unit tests of `create_submodel_graph` against
  hand-built graphs (via `tests/conftest.py::make_graph`): basic extraction,
  edge rewiring for both inbound and outbound boundaries, submodel metadata
  population (canonical child name, copied preamble/preserved blocks,
  `childNodeIds`, ports, and internal `graph` dict), preservation of
  pre-existing submodel entries when adding a new one, name sanitisation
  (`_sanitize_func_name`, case-preserving), strict blank/duplicate/unknown-id
  errors, too-few-node and nesting errors, graph-order child ids,
  bounding-box-centred placeholder placement, selecting every node in
  the graph, multiple input/multiple output/bidirectional port scenarios, the
  no-cross-edges case, and all three nesting-rejection shapes (two submodel
  nodes selected, one submodel node selected, one submodel node mixed with a
  regular node).
- `tests/test_submodel_routes.py` — full FastAPI `TestClient` coverage of all three
  endpoints against an isolated `tmp_path` cwd, with `create_submodel_graph`
  and selected codegen calls mocked where the test isolates the
  route/transaction layer: invalid selections → `400`/`409`; revision
  preconditions; create metadata forwarding; no-clobber and rollback; managed
  child sidecars; pipeline-scoped drill-down with duplicate names; malformed
  encoded backslashes → `400`; dissolve not-found and missing-source failures;
  shared/hand-authored/incompletely-audited file retention;
  authoritative reparse of the child file with sidecar positions preserved;
  submodel deletion; configured-root deletion targeting; and two explicit
  rollback tests (`test_dissolve_sidecar_failure_rolls_back_main_file`,
  `test_dissolve_delete_failure_rolls_back_main_file`) that force a mid-
  transaction failure and assert the parent file's original content and the
  submodel file's existence are both restored.
- `tests/test_submodel_route_contracts.py` — focused end-to-end route contracts
  for stale-revision rejection before transformation, scoped drill-down when
  two parents reuse a submodel name, create no-clobber, and conservative
  dissolve deletion/retention across owned, unowned, shared, and
  incompletely-audited child files.
- `tests/test_submodel_outport_invariant.py` — a single named contract test file
  (not organised by function, but by invariant) pinning that the `out__`
  boundary handle produced by `rewire_edges` is the exact inverse of what
  `flatten_graph` strips, and that codegen's disambiguation between a true
  submodel boundary handle and a regular edge whose port literally happens to
  be named `out__<something>` gates on the *edge's source actually being a
  submodel placeholder node*, not on the string prefix alone. This test
  exists specifically because the individual legs (production, flatten,
  codegen) are each covered piecemeal in their own component's test file, and
  none of those files alone pins the full produce → consume round trip.
- `tests/test_submodel.py` — public flatten/codegen integration, including
  per-child source generation, child preamble and preserved-block round trips,
  compilable parent output, parsing, and request/response model contracts.
- `tests/test_edge_join.py` — create/codegen integration for external
  `edgeJoin` inputs whose stored parent edges share a submodel placeholder but
  whose `out__<child>` handles identify distinct logical base/join sources.
- `tests/test_flatten.py` — direct unit coverage for identity returns,
  flatten-all and targeted flattening, child node/edge dict-vs-model forms,
  internal-edge preservation, strict boundary-handle and child-id validation,
  port-complete rewritten edge ids, edge deduplication, empty/missing child
  graph metadata, and selected-child metadata merging.
- `tests/test_flattening_dedup.py` — parity between parser-driven flattening
  and the shared `flatten_graph` implementation, including single-node,
  multi-node, chained, nested, and hierarchical-then-flat cases.
- `tests/test_pipeline_revision.py` — deterministic document-revision coverage,
  including self-field exclusion and invalidation by parent/child source,
  parent/child sidecar, and canonical graph-config changes.
- `tests/test_submodel_persistence.py` — the shared save transaction's
  submodel-specific filesystem contract: case-insensitive create no-clobber,
  managed child ownership/position sidecars, rejection of request-only
  ownership and orphan-sidecar clobber, source-plus-sidecar deletion, and
  rollback restoration of both deleted files.
- `tests/test_parser_submodels.py` — expression parsing, recursive loading,
  canonical child metadata, cross-boundary port reconstruction, and
  hierarchical/flat parser behaviour.

Canonical-only identities are pinned throughout these suites: submodel paths
are project-relative, boundary edge ids include port identity, and recorded
sidecar paths take precedence over convention-based lookup.
