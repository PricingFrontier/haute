# Submodels — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_submodel_graph.py` | Shared helpers: build a `SUBMODEL` placeholder node, classify cross-boundary edges into input/output ports, rewire edges to/from a placeholder. Used by both the parser's hierarchical merge and the GUI create-submodel operation. |
| `src/haute/_submodel_paths.py` | Validate route-level names, resolve recorded submodel references relative to the active pipeline directory, enforce project containment, and return typed malformed/outside-project errors plus the directory used as config base. |
| `src/haute/_flatten.py` | Dissolve one named submodel or every submodel into a flat graph: validate and consume boundary handles, inline stored child nodes/internal edges, restore authored and edge-join ports, regenerate port-complete edge ids, merge child preamble/preserved blocks, deduplicate exact edges, and retain metadata for untargeted submodels. |
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
  "inputPorts": list[str], "outputPorts": list[str], "graph": dict}` where
  `"graph"` is the submodel's own canonical `PipelineGraph` dump:
  `nodes`, `edges`, `pipeline_name`, `pipeline_description`, `preamble`,
  `preserved_blocks`, and `source_file`.
- `NodeType.SUBMODEL = "submodel"` / `NodeType.SUBMODEL_PORT = "submodelPort"`
  (`_types.py`) — the placeholder node's type; `SUBMODEL_PORT` is used only by
  the drill-down GUI view, not produced by anything in this component.
- Placeholder node shape (`build_submodel_placeholder`): `id =
  f"submodel__{sm_name}"`, `position = {"x": 0, "y": 0}`,
  `data.config = {"file", "childNodeIds", "inputPorts", "outputPorts"}`.
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
  `pipeline_name = "main"`, `pipeline_description`), `CreateSubmodelResponse`
  (`status`, `submodel_file`, `parent_file`, `graph`), `DissolveSubmodelRequest`
  (`submodel_name`, `graph`, `preamble`, `source_file`, `pipeline_name`,
  `pipeline_description`), `DissolveSubmodelResponse` (`status`, `graph`),
  `SubmodelGraphResponse` (`status`, `submodel_name`, `graph`).

## Control flow

### `build_submodel_placeholder(sm_name, sm_file, child_node_ids, input_ports, output_ports, *, description="")`

Pure construction of one `GraphNode`. No validation of its inputs beyond
what the type constructors enforce.

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
  replaced authored source handle moves to `sourcePort`.
- neither inside → passed through unchanged.

Every rewired boundary id appends a deterministic digest of the authored
source/target ports (including when both are absent), so edges sharing endpoints
but carrying distinct hidden handles remain unique. Cross-boundary
reconstruction likewise deduplicates only an exact endpoint-and-authored-port
identity through hierarchical merge and flattening.

### `resolve_submodel_reference(rel_path, *, pipeline_dir, project_root)`

1. Reject an empty or NUL-containing reference with
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

### `flatten_graph(graph, target_name=None)`

1. If `graph.submodels` is falsey, return the original graph object. With a target name, intersect
   it with the metadata keys; if there is no match, also return the original object.
2. Ask `build_edge_join_boundary_target_roles` for any target-port roles that must be restored on
   inbound edges to child edge-join nodes.
3. Pydantic-validate each selected embedded graph, index its child ids, copy
   the parent node/edge lists, remove selected placeholders, and append child
   nodes/internal edges.
4. For every edge incident to a selected placeholder, require the appropriate
   non-empty `out__<child_id>` or `in__<child_id>` handle and verify that the
   child exists in that embedded graph. A mismatch raises `ParseError` with
   edge/submodel/known-child context before rewiring continues.
5. Replace each valid placeholder endpoint with the child id, restore the
   authored `sourceHandle`/`targetHandle` from hidden `sourcePort`/
   `targetPort` (or the edge-join role from step 2), clear the consumed hidden
   field, and regenerate the id with `_edge_id` over endpoints, rendered
   handles, and any hidden ports belonging to an untargeted boundary.
6. Deduplicate only exact six-field edge identities, preserving first
   occurrence. Merge selected child preambles into the root preamble with a
   blank-line separator and append selected child preserved blocks.
7. Return `graph.model_copy(...)`, removing flattened metadata entries and
   setting `submodels=None` when none remain. Untargeted
   placeholders/metadata remain intact.

### `create_submodel_graph(graph, node_ids, name)`

1. `sm_name = _sanitize_func_name(name)`; `sm_file =
   f"modules/{sm_name}.py"`; `selected_ids = set(node_ids)` (de-duplicates and
   drops repeats).
2. Partition `graph.nodes` into `child_nodes` (id in `selected_ids`) and
   `parent_nodes` (the rest) — this is also where ids in `node_ids` that
   don't exist in the graph are silently dropped (they simply never match any
   node).
3. If any `child_nodes` entry has `data.nodeType == NodeType.SUBMODEL`, raise
   `ValueError("Submodels cannot be nested inside other submodels")` — checked
   **before** the size check, so a single selected submodel node raises the
   nesting error, not a "too few nodes" error.
4. If `len(child_node_ids) < 2`, raise `ValueError("A submodel must contain
   at least 2 nodes.")` — this is the post-filter count, so nonexistent or
   duplicate ids that leave fewer than 2 real nodes hit this branch too.
5. Classify `graph.edges` into `internal_edges` (both endpoints in
   `child_node_ids`), `cross_edges` (exactly one endpoint inside — XOR), and
   `external_edges` (neither endpoint inside).
6. `classify_ports` on canonical `cross_edges` →
   `input_ports`, `output_ports`.
7. Build the submodel's canonical internal graph dict: `{"nodes": [child node
   dumps], "edges": [internal edge dumps], "pipeline_name": sm_name,
   "pipeline_description": "", "preamble": "", "preserved_blocks": [],
   "source_file": sm_file}`.
8. `build_submodel_placeholder(...)` → `sm_node`; `rewire_edges(cross_edges,
   sm_node.id, child_node_ids)` → `rewired_cross`.
9. Assemble the new parent graph: `nodes = parent_nodes + [sm_node]`, `edges =
   external_edges + rewired_cross`, `submodels = {**existing, sm_name: {...,
   "graph": sm_graph}}` — existing submodel entries are preserved by starting
   from `dict(graph.submodels or {})`.
10. Return `SubmodelGraphResult(graph=new_graph, sm_file=sm_file,
    sm_name=sm_name, child_node_ids=list(child_node_ids))`.

### `POST /api/submodel/create` (`create_submodel`)

Acquires `save_lock`, runs the body in a threadpool:
1. Compute `sm_filename = f"{_sanitize_func_name(body.name)}.py"`; if
   `is_windows_reserved_filename(sm_filename)`, raise `HTTPException(400,
   <specific message>)` before touching `create_submodel_graph` at all.
2. Call `create_submodel_graph(body.graph, body.node_ids, body.name)`; a
   `ValueError` here is logged (`submodel_create_invalid`, with name and
   node count, `exc_info=True`) and re-raised as `HTTPException(400,
   _INTERNAL_ERROR_DETAIL)` — the specific validation message is never sent
   to the client.
3. Require `body.source_file` truthy, else `HTTPException(400, ...)`.
4. `SavePipelineService(project_root=Path.cwd(),
   pipeline_root=pipeline_dir()).save_graph_transactionally(graph=result.graph,
   name=body.pipeline_name, description=body.pipeline_description or "",
   preamble=body.preamble, source_file=body.source_file)` — this is where
   sanitized-name uniqueness is validated, `graph_to_code_multi` runs, and
   both the parent `.py` and new `modules/<name>.py` are written under the
   save transaction's allowlist and rollback contract (see server-api).
5. Return `CreateSubmodelResponse(status="ok", submodel_file=result.sm_file,
   parent_file=body.source_file, graph=result.graph)`.

### `GET /api/submodel/{name}` (`get_submodel` → `_get_submodel_blocking`)

Acquires `save_lock`, runs in a threadpool:
1. Validate `name` before pipeline discovery; map
   `MalformedSubmodelPathError` to `400`.
2. Iterate `discover_pipelines()` in configured-first order. Parse each
   pipeline inside a per-file `try`: a parse failure is logged as
   `submodel_parent_parse_failed` and skipped. When a parsed pipeline's
   `submodels` metadata contains `name`, resolve that entry's recorded
   `"file"` relative to the pipeline file's parent.
3. If no discovered pipeline records the name, use the
   `modules/<name>.py` convention relative to `pipeline_dir()`.
   `SubmodelPathOutsideProjectError` maps to `403`.
4. `sm_path.is_file()` → else `HTTPException(404, f"Submodel '{name}' not
   found")`.
5. `parse_submodel_file(sm_path, _base_dir=config_base)` → `sm_graph`.
6. `_apply_sidecar_positions(sm_graph, sm_path)` loads sidecar positions and
   applies each matching `(x, y)` via per-node `model_copy`. Parsed nodes
   without a sidecar entry retain their parser-provided position.
7. Return `SubmodelGraphResponse(status="ok", submodel_name=
   sm_graph.pipeline_name or name, graph=sm_graph)`.

### `POST /api/submodel/dissolve` (`dissolve_submodel`)

Acquires `save_lock`, runs the body in a threadpool:
1. Look up `body.submodel_name` in `body.graph.submodels or {}` → `404` if
   absent.
2. Require `body.source_file` and a non-empty string `"file"` in the selected
   metadata; malformed values return `400`.
3. Resolve the recorded file relative to `pipeline_dir()`, mapping typed
   malformed/outside-project path errors to `400`/`403`, and return `404` if
   the file is absent.
4. `parse_submodel_file(sm_path, _base_dir=config_base)`, apply its sidecar
   positions through `_apply_sidecar_positions`, and replace only the
   selected metadata entry's `"graph"` with that authoritative disk graph.
   Copy `body.preamble` onto the parent graph before flattening.
5. `flatten_graph(authoritative_graph, target_name=sm_name)` → `flat`. A
   stale boundary referencing a child no longer present on disk raises
   `ParseError`; any other submodels remain placeholders. Child preamble and
   preserved blocks are now part of `flat`.
6. `SavePipelineService(...).save_graph_transactionally(graph=flat,
   preamble=flat.preamble, ..., delete_module_files=[sm_file])` validates the
   final graph, rewrites the parent, and deletes the authoritative child
   through the same transaction.
7. Return `DissolveSubmodelResponse(status="ok", graph=flat)`.

## Edge cases and invariants

- **Duplicate/nonexistent node ids in `node_ids`** are absorbed by
  `set(node_ids)` and by the fact that only ids matching an actual graph node
  contribute to `child_node_ids` — both collapse into the same "fewer than 2
  real nodes" `ValueError`, with no distinct "unknown id" error path.
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
  The handle must use `in__`/`out__`, name a child in the selected embedded
  graph, and be present whenever an endpoint is a selected placeholder.
  Violations raise contextual `ParseError`; exact six-field edges alone are
  deduplicated after valid rewiring.
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

## Error handling

| Situation | Exception / status | Where it surfaces |
|---|---|---|
| `create_submodel_graph` validation failure (too few nodes, nesting) | `ValueError` → `HTTPException(400, _INTERNAL_ERROR_DETAIL)` | `routes/submodel.py::create_submodel` — logged as `submodel_create_invalid` with full detail server-side first. |
| Submodel name collides with a Windows reserved device filename | `HTTPException(400, <specific message>)` | `create_submodel`, before `create_submodel_graph` runs. |
| Missing `source_file` on create or dissolve | `HTTPException(400, "source_file is required...")` | `create_submodel` / `dissolve_submodel`. |
| Dissolve target not in `graph.submodels` | `HTTPException(404, f"Submodel '{sm_name}' not found in graph")` | `dissolve_submodel`. |
| Drill-down target `.py` file missing | `HTTPException(404, f"Submodel '{name}' not found")` | `_get_submodel_blocking`. |
| Empty/NUL-containing reference or route name containing `/` or `\` | `MalformedSubmodelPathError` → `HTTPException(400)` | `_submodel_paths.py`, mapped by drill-down/dissolve. |
| Recorded reference resolves outside project root | `SubmodelPathOutsideProjectError` → `HTTPException(403)` | `_submodel_paths.py`, mapped by drill-down/dissolve. |
| Malformed/missing boundary handle or unknown child passed to `flatten_graph` | `ParseError` with edge/submodel context | `_flatten._boundary_child_id`; dissolve stops before save/delete. |
| Any write step in the underlying save transaction fails (config write, sidecar write, module delete) | Best-effort rollback by `SavePipelineService`, original error re-raised | Surfaces as `HTTPException(500, ...)`; a failed compensating operation is logged and can leave partial state. See [server-api](../server-api/high-level.md). |

## Testing

Tests live in `tests/test_submodel_graph.py`, `tests/test_submodel_ops.py`,
`tests/test_submodel_routes.py`, `tests/test_submodel_outport_invariant.py`,
`tests/test_submodel.py`, `tests/test_flatten.py`, and
`tests/test_flattening_dedup.py`, with related parser coverage in
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
  population (canonical child name/description, preamble, preserved blocks,
  `childNodeIds`, ports, and internal `graph` dict), preservation of
  pre-existing submodel entries when adding a new one, name sanitisation
  (`_sanitize_func_name`, case-preserving), the too-few-nodes and
  empty-selection `ValueError`s (including the nonexistent-id and
  duplicate-id paths that reduce to the same error), selecting every node in
  the graph, multiple input/multiple output/bidirectional port scenarios, the
  no-cross-edges case, and all three nesting-rejection shapes (two submodel
  nodes selected, one submodel node selected, one submodel node mixed with a
  regular node).
- `tests/test_submodel_routes.py` — full FastAPI `TestClient` coverage of all three
  endpoints against an isolated `tmp_path` cwd, with `create_submodel_graph`
  and selected codegen calls mocked where the test isolates the
  route/transaction layer: invalid selections → `400`; create metadata
  forwarding; output allowlisting and rollback; nested pipeline roots;
  drill-down through a recorded custom child path; broken sibling pipelines
  skipped during lookup; malformed encoded backslashes → `400`;
  configured-root fallback; dissolve not-found and missing-source failures;
  authoritative reparse of the child file with sidecar positions preserved;
  submodel deletion; configured-root deletion targeting; and two explicit
  rollback tests (`test_dissolve_sidecar_failure_rolls_back_main_file`,
  `test_dissolve_delete_failure_rolls_back_main_file`) that force a mid-
  transaction failure and assert the parent file's original content and the
  submodel file's existence are both restored.
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
- `tests/test_flatten.py` — direct unit coverage for identity returns,
  flatten-all and targeted flattening, child node/edge dict-vs-model forms,
  internal-edge preservation, strict boundary-handle and child-id validation,
  port-complete rewritten edge ids, edge deduplication, empty/missing child
  graph metadata, and selected-child metadata merging.
- `tests/test_flattening_dedup.py` — parity between parser-driven flattening
  and the shared `flatten_graph` implementation, including single-node,
  multi-node, chained, nested, and hierarchical-then-flat cases.
- `tests/test_parser_submodels.py` — expression parsing, recursive loading,
  canonical child metadata, cross-boundary port reconstruction, and
  hierarchical/flat parser behaviour.

Canonical-only identities are pinned throughout these suites: submodel paths
are project-relative, boundary edge ids include port identity, and recorded
sidecar paths take precedence over convention-based lookup.
