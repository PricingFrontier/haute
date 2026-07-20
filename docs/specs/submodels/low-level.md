# Submodels — Low-Level Specification

## Module map

| File | Responsibility |
|---|---|
| `src/haute/_submodel_graph.py` | Shared helpers: build a `SUBMODEL` placeholder node, classify cross-boundary edges into input/output ports, rewire edges to/from a placeholder. Used by both the parser's hierarchical merge and the GUI create-submodel operation. |
| `src/haute/_submodel_paths.py` | Resolve a submodel file reference (`modules/<name>.py`) to an absolute path plus its config base directory, honouring pipeline-local vs. project-root precedence. |
| `src/haute/_flatten.py` | Dissolve one named submodel or every submodel into a flat graph: inline stored child nodes/internal edges, consume boundary handles, restore edge-join target roles, deduplicate edges, and retain metadata for untargeted submodels. |
| `src/haute/routes/_submodel_ops.py` | Pure (no I/O) graph transform: extract selected nodes out of a `PipelineGraph` into a new submodel, producing the updated parent graph and submodel metadata. |
| `src/haute/routes/submodel.py` | FastAPI router (`/api/submodel/*`): `POST /create`, `GET /{name}`, `POST /dissolve`. Wires validation, the pure transform, and the shared save transaction together; owns all file I/O and HTTP error mapping. |

Related but external to this component:
- `src/haute/_parser_submodels.py` (expression-parsing) — parses
  `pipeline.submodel(...)` calls and submodel `.py` files, and calls into
  `src/haute/_submodel_graph.py`'s same three helpers to build the hierarchical view at
  parse time.
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
  `"graph"` is the submodel's own internal `nodes`/`edges`/`submodel_name`/
  `submodel_description`/`source_file`.
- `NodeType.SUBMODEL = "submodel"` / `NodeType.SUBMODEL_PORT = "submodelPort"`
  (`_types.py`) — the placeholder node's type; `SUBMODEL_PORT` is used only by
  the drill-down GUI view, not produced by anything in this component.
- Placeholder node shape (`build_submodel_placeholder`): `id =
  f"submodel__{sm_name}"`, `position = {"x": 0, "y": 0}`,
  `data.config = {"file", "childNodeIds", "inputPorts", "outputPorts"}`.
- Boundary edge handles: `targetHandle = f"in__{child_id}"` on an edge whose
  target is the placeholder; `sourceHandle = f"out__{child_id}"` on an edge
  whose source is the placeholder. These are the only two synthetic handle
  shapes this component produces or consumes.
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

Iterates `cross_edges` (accepts 2-, 3-, or 4-tuples — only `edge[0]`/`edge[1]`
are read, extra fields such as source-port are ignored here) and buckets each
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
  whichever pass ran first.
- source inside only → source becomes `sm_node_id`, `sourceHandle =
  f"out__{e.source}"`, `targetHandle` preserved for the same reason.
- neither inside → passed through unchanged.

New edge ids: `f"e_{e.source}_{sm_node_id}__{e.target}"` (inbound rewire) /
`f"e_{sm_node_id}_{e.target}__{e.source}"` (outbound rewire) — deterministic
given the same input edge and placeholder id.

### `resolve_submodel_reference(rel_path, *, pipeline_dir, project_root)`

1. `resolved_root = project_root.resolve()`; `active_dir = (pipeline_dir or
   project_root).resolve()`.
2. Build `local_path = (active_dir / rel_path).resolve()` and `project_path =
   (resolved_root / rel_path).resolve()`.
3. For **both** candidates: if not `is_relative_to(resolved_root)`, raise
   `ValueError` immediately (before any filesystem check) — this makes both
   returned-path branches below unconditionally safe.
4. If `local_path.is_file()`: return `(local_path, active_dir)` — the
   pipeline-local module wins when it actually exists.
5. Elif `project_path.is_relative_to(active_dir)`: return `(project_path,
   active_dir)` — an explicit project-root-prefixed reference that still
   happens to live under the active pipeline directory keeps that directory
   as its config base.
6. Else: return `(project_path, resolved_root)` — legacy single-file-project
   fallback; the config base is the project root, not the (irrelevant)
   pipeline directory.

`resolve_submodel_by_name(name, *, pipeline_dir, project_root)` is a thin
wrapper calling the above with `rel_path=f"modules/{name}.py"` — this is the
exact preference order `_parser_submodels.py` uses for `pipeline.submodel()`
imports, so drill-down and the actually-loaded pipeline never disagree about
which file is authoritative.

### `flatten_graph(graph, target_name=None)`

1. If `graph.submodels` is falsey, return the original graph object. With a target name, intersect
   it with the metadata keys; if there is no match, also return the original object.
2. Ask `build_edge_join_boundary_target_roles` for any target-port roles that must be restored on
   inbound edges to child edge-join nodes.
3. Copy the parent node/edge lists, remove placeholders for the selected names, and append each
   selected submodel's stored child nodes/internal edges. Dict entries are Pydantic-validated;
   existing `GraphNode`/`GraphEdge` objects are reused.
4. For each edge, when its source is a selected placeholder and `sourceHandle` is non-empty, set
   the source to `sourceHandle.removeprefix("out__")`, clear `sourceHandle`, and regenerate the id.
   Apply the analogous `targetHandle.removeprefix("in__")` rewrite on selected placeholder
   targets, restoring a target role from step 2 when available.
5. Drop any edge that still references a selected placeholder (notably a boundary edge with a
   missing handle), then deduplicate by `(source, target, sourceHandle, targetHandle)` preserving
   first occurrence.
6. Return `graph.model_copy(...)`, removing flattened metadata entries and setting `submodels=None`
   when none remain. Untargeted placeholders/metadata remain intact.

The flattener does not validate the expected prefix or child membership: any non-empty malformed
handle is consumed verbatim after `removeprefix`, and no explicit exception is raised. Codegen has
the stricter prefix/membership validation gate.

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
6. `classify_ports` on `cross_edges` (as `(source, target)` pairs) →
   `input_ports`, `output_ports`.
7. Build the submodel's internal graph dict: `{"nodes": [child node dumps],
   "edges": [internal edge dumps], "submodel_name": sm_name,
   "submodel_description": "", "source_file": sm_file}`.
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
1. `SavePipelineService._validate_unique_sanitized_names(body.graph)` — fails
   loud on name collisions in the *pre-transform* graph.
2. Compute `sm_filename = f"{_sanitize_func_name(body.name)}.py"`; if
   `is_windows_reserved_filename(sm_filename)`, raise `HTTPException(400,
   <specific message>)` before touching `create_submodel_graph` at all.
3. Call `create_submodel_graph(body.graph, body.node_ids, body.name)`; a
   `ValueError` here is logged (`submodel_create_invalid`, with name and
   node count, `exc_info=True`) and re-raised as `HTTPException(400,
   _INTERNAL_ERROR_DETAIL)` — the specific validation message is never sent
   to the client.
4. Require `body.source_file` truthy, else `HTTPException(400, ...)`.
5. `SavePipelineService(project_root=Path.cwd(),
   pipeline_root=pipeline_dir()).save_graph_transactionally(graph=result.graph,
   name=body.pipeline_name, description=body.pipeline_description or "",
   preamble=body.preamble, source_file=body.source_file)` — this is where
   `graph_to_code_multi` actually runs and both the parent `.py` and the new
   `modules/<name>.py` are written, under the save transaction's allowlist and
   rollback contract (see server-api).
6. Return `CreateSubmodelResponse(status="ok", submodel_file=result.sm_file,
   parent_file=body.source_file, graph=result.graph)`.

### `GET /api/submodel/{name}` (`get_submodel` → `_get_submodel_blocking`)

Acquires `save_lock`, runs in a threadpool:
1. `project_root = Path.cwd()`; `active_pipeline_dir = pipeline_dir()`;
   `resolve_submodel_by_name(name, ...)` → `(sm_path, config_base)`.
2. Re-resolve `project_root` and check `sm_path.is_relative_to(project_root)`
   → `HTTPException(403)` if not (see high-level spec's NOTE — this branch is
   unreachable given step 1's guarantees and the single-path-segment `{name}`
   constraint).
3. `sm_path.is_file()` → else `HTTPException(404, f"Submodel '{name}' not
   found")`.
4. `parse_submodel_file(sm_path, _base_dir=config_base)` → `sm_graph`.
5. `load_sidecar_positions(sm_path)` → apply any stored `(x, y)` position
   onto matching node ids via `model_copy`. `sm_graph.nodes` is rebuilt via
   `model_copy` whenever the submodel has at least one node (i.e.
   unconditionally, given the 2-node minimum) — the `if updated_nodes:`
   guard only skips the rebuild for a degenerate empty node list, not for
   "no sidecar positions found".
6. Return `SubmodelGraphResponse(status="ok", submodel_name=
   sm_graph.pipeline_name or name, graph=sm_graph)`.

### `POST /api/submodel/dissolve` (`dissolve_submodel`)

Acquires `save_lock`, runs the body in a threadpool:
1. Look up `body.submodel_name` in `body.graph.submodels or {}` → `404` if
   absent.
2. `sm_file = submodels[sm_name].get("file", "")`.
3. `flatten_graph(body.graph, target_name=sm_name)` → `flat` — only the
   targeted submodel is dissolved, any other submodels in the graph are left
   as placeholders.
4. `SavePipelineService._validate_unique_sanitized_names(flat)` — validated
   **post**-inline, since inlining can itself introduce a name collision with
   an existing parent node.
5. Require `body.source_file` truthy, else `HTTPException(400, ...)`.
6. `SavePipelineService(...).save_graph_transactionally(graph=flat, ...,
   delete_module_files=[sm_file] if sm_file else ())` — the submodel `.py`
   file is deleted as part of the same transaction that rewrites the parent
   file; see server-api for the rollback contract if the delete fails after
   the rewrite (or vice versa).
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
- **Cross-submodel edge rewiring is order-independent for handle
  preservation but not for edge identity.** `rewire_edges` is called once per
  submodel being processed; a `child-of-A → child-of-B` edge is rewired twice
  across two separate `rewire_edges` calls (once when A is processed, once
  when B is), and each pass must not clobber the opposite side's
  already-set handle to `None` — this is why `sourceHandle`/`targetHandle`
  are explicitly *preserved from the input edge*, not always cleared, on the
  side that isn't being rewired in that pass.
- **Flatten is identity-preserving on no-op calls.** With no submodel metadata, an empty metadata
  dict, or a `target_name` absent from the metadata, `flatten_graph` returns the exact input object
  (`is`, not merely equality).
- **Flatten is permissive on malformed boundary handles.** It gates on the endpoint being a
  selected placeholder and the relevant handle being truthy, but does not check `in__`/`out__` or
  that the derived id belongs to the submodel. A wrong-prefix handle becomes an endpoint id
  verbatim; a missing handle leaves the placeholder reference in place and that edge is silently
  discarded. Internal and rewired edges are then deduplicated by the four endpoint/handle fields.
- **Inbound edge-join roles survive flattening.** An `in__<child>` boundary targeting an
  edge-join child recovers the base/join `targetHandle` through
  `build_edge_join_boundary_target_roles`; ordinary inbound boundaries clear the synthetic handle
  to `None`.
- **`_submodel_paths.py`'s escapes-project-directory check runs against
  *both* the local and project-root candidate paths, before either is
  filesystem-checked** — a relative reference that would escape via either
  resolution order fails closed rather than only checking the one that ends
  up used.
- **The Windows reserved-name check is platform-unconditional.** It runs on
  every OS at create time (not just Windows), so a pipeline saved on
  Linux/macOS cannot mint a submodel that becomes unloadable on a Windows
  checkout.
- **`get_submodel` re-applies sidecar positions per-node, not per-graph** —
  each node is individually `model_copy`'d only if its id has a stored
  position (nodes with no sidecar entry keep whatever position the parser
  produced), but the resulting node list is then always reassigned onto
  `sm_graph` via one outer `model_copy` as long as the submodel has any
  nodes at all; there is no optimisation that skips the outer rebuild when
  `positions` is empty.

## Error handling

| Situation | Exception / status | Where it surfaces |
|---|---|---|
| `create_submodel_graph` validation failure (too few nodes, nesting) | `ValueError` → `HTTPException(400, _INTERNAL_ERROR_DETAIL)` | `routes/submodel.py::create_submodel` — logged as `submodel_create_invalid` with full detail server-side first. |
| Submodel name collides with a Windows reserved device filename | `HTTPException(400, <specific message>)` | `create_submodel`, before `create_submodel_graph` runs. |
| Missing `source_file` on create or dissolve | `HTTPException(400, "source_file is required...")` | `create_submodel` / `dissolve_submodel`. |
| Dissolve target not in `graph.submodels` | `HTTPException(404, f"Submodel '{sm_name}' not found in graph")` | `dissolve_submodel`. |
| Drill-down target `.py` file missing | `HTTPException(404, f"Submodel '{name}' not found")` | `_get_submodel_blocking`. |
| Drill-down path escapes project root | `HTTPException(403, ...)` | `_get_submodel_blocking` — see the NOTE in the high-level spec; not reachable via this route in practice. |
| `resolve_submodel_reference` candidate path escapes project root | `ValueError` | `_submodel_paths.py` — propagates uncaught from `_get_submodel_blocking` if ever reached (no route-level catch); reachable only from callers that pass a `rel_path` containing path separators, which this route's single-segment `{name}` parameter cannot produce. |
| Malformed/missing boundary handle passed directly to `flatten_graph` | No dedicated exception | Wrong-prefix non-empty handles are consumed as endpoint ids; edges still naming a removed placeholder are dropped. Codegen validates persisted graphs more strictly. |
| Any write step in the underlying save transaction fails (config write, sidecar write, module delete) | Rolled back by `SavePipelineService`, re-raised | Surfaces as `HTTPException(500, ...)` from `save_graph_transactionally`; see [server-api](../server-api/high-level.md) low-level spec for the transaction/rollback mechanics. |

## Testing

Tests live in `tests/test_submodel_graph.py`, `tests/test_submodel_ops.py`,
`tests/test_submodel_routes.py`, and `tests/test_submodel_outport_invariant.py`,
plus `tests/test_flatten.py`, with related parser coverage in
`tests/test_parser_submodels.py` for the neighbouring expression-parsing component.

- `test_submodel_graph.py` — pure unit tests of the three
  `_submodel_graph.py` functions in isolation: placeholder construction
  (id/type/config shape, description passthrough including special
  characters, empty ports, empty child list); `classify_ports` (basic
  classification, dedup with order preservation, no-cross-edges, a node
  being both input and output, all-internal edges producing no ports);
  `rewire_edges` (internal drop, external passthrough, inbound/outbound
  rewire, mixed edge sets, deterministic edge ids, multiple inbound/outbound
  edges to/from the same child, and the cross-submodel two-pass handle
  preservation case).
- `test_submodel_ops.py` — unit tests of `create_submodel_graph` against
  hand-built graphs (via `tests/conftest.py::make_graph`): basic extraction,
  edge rewiring for both inbound and outbound boundaries, submodel metadata
  population (`childNodeIds`, ports, internal `graph` dict), preservation of
  pre-existing submodel entries when adding a new one, name sanitisation
  (`_sanitize_func_name`, case-preserving), the too-few-nodes and
  empty-selection `ValueError`s (including the nonexistent-id and
  duplicate-id paths that reduce to the same error), selecting every node in
  the graph, multiple input/multiple output/bidirectional port scenarios, the
  no-cross-edges case, and all three nesting-rejection shapes (two submodel
  nodes selected, one submodel node selected, one submodel node mixed with a
  regular node).
- `test_submodel_routes.py` — full FastAPI `TestClient` coverage of all three
  endpoints against an isolated `tmp_path` cwd, with `create_submodel_graph`
  and `codegen.graph_to_code_multi`/`graph_to_code` mocked to isolate the
  route/transaction layer from the graph-transform and codegen layers:
  invalid node ids and too-few-nodes → `400`; happy-path create including
  `pipeline_description` forwarding to codegen; the same output-path
  allowlist and rollback-on-disallowed-path behaviour the manual save
  endpoint has (`test_create_rejects_unallowlisted_codegen_path_and_rolls_back`);
  writing modules/configs beside a configured nested pipeline root rather
  than the project root; drill-down not-found, dotted/traversal-looking names
  resolving safely to a plain `404` rather than an error, configured-root and
  legacy-root-fallback module resolution matching the parser's own
  preference; dissolve not-found, missing `source_file`, happy path
  (including `pipeline_description` forwarding), submodel file deletion,
  configured-root vs. legacy-root deletion targeting, and two explicit
  rollback tests (`test_dissolve_sidecar_failure_rolls_back_main_file`,
  `test_dissolve_delete_failure_rolls_back_main_file`) that force a mid-
  transaction failure and assert the parent file's original content and the
  submodel file's existence are both restored.
- `test_submodel_outport_invariant.py` — a single named contract test file
  (not organised by function, but by invariant) pinning that the `out__`
  boundary handle produced by `rewire_edges` is the exact inverse of what
  `flatten_graph` strips, and that codegen's disambiguation between a true
  submodel boundary handle and a regular edge whose port literally happens to
  be named `out__<something>` gates on the *edge's source actually being a
  submodel placeholder node*, not on the string prefix alone. This test
  exists specifically because the individual legs (production, flatten,
  codegen) are each covered piecemeal in their own component's test file, and
  none of those files alone pins the full produce → consume round trip.
- `test_flatten.py` — direct unit coverage for identity returns, flatten-all and targeted
  flattening, child node/edge dict-vs-model forms, internal-edge preservation, boundary rewiring
  and handle clearing, silent drop of a still-placeholder edge, edge deduplication, empty/missing
  child graph metadata, and preservation of unrelated `PipelineGraph` metadata.

Known coverage gap: `tests/test_flatten.py` pins silent dropping for a missing handle, but does not
directly pin the wrong-prefix non-empty `removeprefix` case or an unknown derived child id. The
adjacent `src/haute/_parser_submodels.py` remains owned and covered by expression-parsing.
