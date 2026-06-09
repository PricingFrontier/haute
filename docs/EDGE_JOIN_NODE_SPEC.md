# Edge-Join Node Spec

Status: implemented in the current branch for v1
Owner: Graph editing / dataframe transforms workstream
Last updated: 2026-06-09

## Problem

Users often need to enrich the dataframe flowing along an existing pipeline edge
with columns from another dataframe. Today they can do this with a regular
Polars node that has two incoming edges and custom join code, but the visual
workflow makes a common modelling action feel heavier than it should be.

The desired interaction is:

1. A user drags from a source node or source handle.
2. Instead of dropping onto a node, they drop onto the middle of an existing
   edge, or onto another output handle when there is no edge to target.
3. Haute creates a small `edge-join` node at that point.
4. The original edge is split through the new node.
5. The dragged input becomes the second join input.
6. Everything else behaves like a normal node: config panel, preview, trace,
   save/load, codegen, parser, execution, cache invalidation, and deploy.

The key constraint is that this must remain a normal DAG. Edges should not gain
hidden execution semantics, and the backend should not need a second graph
model for edge-attached transforms.

## Core Model

`edge-join` is a first-class node type with special creation behaviour and a
smaller canvas rendering.

When the user drops a connection from `C` onto the existing edge `A -> B`,
Haute materialises a normal node `J` and rewrites:

```text
Before:

A -> B
C

After:

A -> J -> B
C -> J
```

`A -> J` is the base or spine input. It preserves the row shape that was already
flowing along the original edge.

`C -> J` is the join input. It provides lookup/enrichment columns.

By representing the result as real nodes and edges, existing execution,
preview, trace, codegen, parser, persistence, undo/redo, and deploy paths can
work with the graph using the same assumptions they use for every other node.

## Naming

The user-facing name is `edge-join`.

Implementation names should follow existing project conventions:

- React/backend node type value: `edgeJoin`
- Python decorator: `@pipeline.edge_join`
- Config folder, if sidecars are used later: `config/edge_join/`
- Display label badge: `JOIN`
- Palette entry: none in v1

The node is not palette-created. It is created by joining onto an existing edge
or by connecting one node output to another node output.

## User Experience

### Creating An Edge-Join

The happy path:

1. User begins a connection from a source handle.
2. Existing edges become valid drop targets.
3. Hovering a compatible edge shows a small insertion marker at the nearest
   point on the edge.
4. Releasing on that edge creates the edge-join node at the marker.
5. The new node is selected and its config panel opens.
6. The lower preview panel behaves exactly like it does for regular nodes.

The alternate path for disconnected outputs:

1. User begins a connection from one source handle.
2. User drops it on another source handle.
3. Haute creates an edge-join node between the two source nodes.
4. The drop target becomes the base input and the dragged source becomes the
   join input by default.
5. The user can swap base and join roles in the editor; the UI handles and
   config update together.

The created node should be positioned at the drop point in flow coordinates,
with a small offset only if needed to avoid exact overlap with another node.

The original downstream node remains downstream of the inserted join. From the
user's point of view, the flow along the old edge continues, just with an
enrichment point inserted into the line.

### Visual Shape

The node should use the normal node data contract but a compact rendering:

- about 72-96 px wide and 36-48 px high at normal zoom;
- a small join/link icon;
- a concise label such as `Join` or the configured join key;
- left target handles for both inputs, visually stacked or separated;
- one right source handle;
- normal selected, hover, trace-active, trace-dimmed, error, and running states.

The compact node must still be large enough to:

- click reliably;
- drag without accidentally starting a new connection;
- show a status marker;
- support keyboard selection/deletion;
- remain visible at low zoom.

It should not be a decorative dot on an edge. It is a node.

### Repeated Joins On The Same Flow

Users can join more than once along the same branch:

```text
A -> J1 -> J2 -> B
C -> J1
D -> J2
```

Dropping a new join onto `A -> J1`, `J1 -> B`, or any later split segment should
insert another edge-join into that segment. There is no global "only one join
per original edge" rule.

### Deleting An Edge-Join

Deleting an edge-join should use the same deletion flow as other nodes, with one
extra ergonomic behaviour worth considering:

- If the edge-join has exactly one base input and one outgoing edge, deletion
  may reconnect base input directly to the downstream target.
- The join input edge should be removed.
- If the node has ambiguous topology, deletion should behave like normal node
  deletion and remove connected edges without guessing.

This reconnect-on-delete is optional for v1. If implemented, it must be tested
as a graph rewrite, not as an implicit fallback.

## Graph Rewrite

Given:

```json
{
  "id": "e_A_B",
  "source": "A",
  "target": "B",
  "sourceHandle": "policies",
  "targetHandle": "in"
}
```

and a new dragged connection from source `C`, create node `J`:

```json
{
  "id": "edgeJoin_12",
  "type": "edgeJoin",
  "position": { "x": 420, "y": 180 },
  "data": {
    "label": "Join 12",
    "description": "",
    "nodeType": "edgeJoin",
    "config": {
      "baseInput": "A",
      "joinInput": "C",
      "how": "left",
      "on": [],
      "leftOn": [],
      "rightOn": [],
      "suffix": "_right"
    }
  }
}
```

and replace edges with:

```json
[
  {
    "id": "e_A_edgeJoin_12",
    "source": "A",
    "target": "edgeJoin_12",
    "sourceHandle": "policies",
    "targetHandle": "base"
  },
  {
    "id": "e_C_edgeJoin_12",
    "source": "C",
    "target": "edgeJoin_12",
    "sourceHandle": null,
    "targetHandle": "join"
  },
  {
    "id": "e_edgeJoin_12_B",
    "source": "edgeJoin_12",
    "target": "B",
    "sourceHandle": null,
    "targetHandle": "in"
  }
]
```

Important preservation rules:

- Preserve the original edge's `sourceHandle` on `A -> J`.
- Preserve the original edge's `targetHandle` on `J -> B`.
- The new dragged edge preserves its own `sourceHandle` on `C -> J`.
- The edge-join's target handles should be stable names: `base` and `join`.
- If the original edge crosses a submodel boundary, keep the boundary handle on
  the corresponding rewritten segment.
- Do not create the node if the rewrite would introduce a cycle.
- Do not silently coerce empty handles; existing `GraphEdge` validation should
  continue to reject empty strings.

## Config Schema

V1 config:

```json
{
  "baseInput": "A",
  "joinInput": "C",
  "how": "left",
  "on": ["policy_id"],
  "leftOn": [],
  "rightOn": [],
  "suffix": "_right",
  "coalesce": null,
  "validate": null,
  "maintainOrder": null
}
```

Field semantics:

- `baseInput`: node id of the original edge source.
- `joinInput`: node id of the source dropped onto the edge.
- `how`: Polars join type. V1 should support `left`, `inner`, `full`,
  `semi`, `anti`, and `cross` only if the runtime Polars version supports the
  selected value.
- `on`: same-named join columns. Mutually exclusive with `leftOn`/`rightOn`.
- `leftOn`: join column names on the base input.
- `rightOn`: join column names on the join input.
- `suffix`: suffix for duplicate right-side column names.
- `coalesce`, `validate`, `maintainOrder`: optional advanced Polars join
  options. They can be hidden under an advanced UI section in v1.

The default `how` should be `left` because inserting into an existing edge
should preserve the base branch's row grain unless the user deliberately
changes it.

The config should store node ids for input roles, not labels. Labels can change;
node ids define graph identity.

If a user rewires an edge-join manually, the UI should update `baseInput` and
`joinInput` only when the role is unambiguous from `targetHandle`. If it is not
unambiguous, validation should fail loudly and ask the user to reconnect the
two inputs.

Frontend JSON should use camel-case keys to match the current graph config
style. Python decorators should use snake-case kwargs:

```python
@pipeline.edge_join(
    base_input="quotes",
    join_input="area_lookup",
    left_on=["area_code"],
    right_on=["area"],
)
```

Parser and config-builder code should normalise the snake-case decorator kwargs
back into the canonical graph config shape.

## Backend Semantics

### NodeType And Registry

Add `NodeType.EDGE_JOIN = "edgeJoin"` and register it everywhere a real node
type is expected:

- `DECORATOR_TO_NODE_TYPE`
- `NODE_TYPE_TO_DECORATOR`
- exec builder registry
- codegen builder registry
- column contract registry
- config validation and config-builder allow-lists
- chunk capability declarations, if the chunking coverage gate requires every
  node type to declare capability
- frontend node metadata

The existing registry completeness checks are useful here: adding the enum
without all registrations should fail loudly.

### Execution Builder

The exec builder should:

1. Require exactly two incoming frames.
2. Resolve the base and join frames by `source_ids` and config role fields.
3. Raise a clear config error if `baseInput` or `joinInput` is missing, stale,
   duplicated, or not connected.
4. Validate join key configuration:
   - `cross` joins must not specify keys.
   - non-cross joins must specify either `on` or both `leftOn` and `rightOn`.
   - `on` cannot be combined with `leftOn` or `rightOn`.
   - `leftOn` and `rightOn` must have the same length.
5. Return `base.join(join, ...)` as a `pl.LazyFrame`.

It should not fall back to "first input joins second input" when config roles
are stale. That would hide graph corruption.

### Column Contract

The first version may declare the edge-join opaque because join output depends
on runtime schemas, selected columns, suffix collision behaviour, and join
options.

If a more precise contract is added later, it should model:

- all base columns pass through;
- right-side columns are added except duplicate key columns and duplicate names
  renamed with suffix;
- referenced columns are the configured join keys, ideally per parent through
  `inputs_by_parent`.

Opaque is acceptable for v1 if it is explicit in the registry.

### Codegen

Generated code should be explicit and readable:

```python
@pipeline.edge_join(how="left", on=["policy_id"], suffix="_right")
def join_lookup(base: pl.LazyFrame, lookup: pl.LazyFrame) -> pl.LazyFrame:
    """Optional user-authored description."""
    return base.join(lookup, on=["policy_id"], how="left", suffix="_right")
```

For asymmetric keys:

```python
@pipeline.edge_join(how="left", left_on=["policy_id"], right_on=["id"], suffix="_lookup")
def join_lookup(base: pl.LazyFrame, lookup: pl.LazyFrame) -> pl.LazyFrame:
    """Optional user-authored description."""
    return base.join(
        lookup,
        left_on=["policy_id"],
        right_on=["id"],
        how="left",
        suffix="_lookup",
    )
```

The generated function parameter order should be base first, join second,
regardless of edge declaration order. The codegen layer already receives
`source_ids`; if needed, `_node_to_code` should pass enough role context to the
edge-join builder to order parameters by `baseInput` and `joinInput`.

Generated `pipeline.connect(...)` calls remain normal graph wiring. Edge-join
does not require a new connection syntax beyond preserving existing
`source_port` handling.

### Parser

Parser support should mirror other node types:

- parse `@pipeline.edge_join(...)`;
- preserve description;
- extract config kwargs into the node config;
- infer code-free join body back into structured config without storing
  generated boilerplate as custom code;
- preserve explicit graph edges and `source_port` metadata.

If parser support for role fields cannot infer `baseInput` and `joinInput` from
the decorator alone, codegen should emit them as decorator kwargs:

```python
@pipeline.edge_join(base_input="quotes", join_input="area_lookup", how="left", on=["area"])
```

Using node/function names in generated Python is acceptable because parsed
source has names rather than React Flow ids. On parse, those values become node
ids in the resulting graph because function names are graph ids.

### Pipeline API

Add:

```python
def edge_join(self, fn: Callable | None = None, **config: Any) -> Callable:
    return self._register_node(fn, _node_type=NodeType.EDGE_JOIN, **config)
```

Also ensure `Pipeline.connect(..., source_port=...)` is accepted consistently
with generated code and parser support. Port-aware edge round-tripping already
exists in codegen/parser tests, and edge-join should build on that rather than
introduce another persistence path.

## Frontend Semantics

### Node Metadata

Add `edgeJoin` to `NODE_TYPES` and `NODE_TYPE_META`, but do not include it in
`PALETTE_TYPES`.

Suggested metadata:

- icon: `Merge`, `GitMerge`, `Combine`, or the closest lucide equivalent in the
  installed version;
- color: reuse transform-group color or a nearby distinct transform accent;
- label: `JOIN`;
- name: `Edge Join`;
- description: `Join another dataframe into this edge`;
- default config: same as the v1 config defaults;
- max inputs: `2`.

### Compact Node Rendering

`PipelineNode` can either branch on `nodeType === NODE_TYPES.EDGE_JOIN` or a
new `shape: "compact"` metadata option can be added. Prefer a metadata-driven
shape if other compact nodes are likely.

Requirements:

- target handles `base` and `join` on the left side;
- source handle on the right;
- stable dimensions across zoom states;
- no body labels that can overflow;
- same trace/status visual language as regular nodes.

### Edge Drop Targeting

React Flow edges are not nodes, so the creation interaction needs a custom
edge-hit layer.

Acceptable approaches:

1. Custom edge type that renders a wider invisible stroke over each edge and
   reports hover/drop events.
2. Pane-level `onConnectEnd` handling that computes nearest edge segment from
   pointer position and inserts when within a threshold.

The custom edge type is likely cleaner for visual feedback. The nearest-edge
approach can be simpler if React Flow connection-end events provide enough
information in the current version.

The hit threshold should be generous enough to feel intentional, but the user
should get a clear hover marker before release. Creation without a visible
target should not occur.

### Config Panel

The right panel should reuse existing editor patterns:

- input source chips for base and join inputs;
- join type select;
- join key selector using upstream column metadata;
- support for same-named keys through `on`;
- support for asymmetric keys through paired `leftOn`/`rightOn` rows;
- suffix input;
- advanced section for `coalesce`, `validate`, and `maintainOrder`;
- delete-input controls should delete the relevant incoming edge and surface a
  validation error until the role is reconnected.

The editor should not expose arbitrary Polars code in v1. If users need custom
logic, they can insert a normal Polars node before or after the edge-join.

### Preview

Previewing an edge-join should use the normal selected-node preview path.

The preview request should fail loudly with a node-level error if:

- fewer or more than two inputs are connected;
- required role handles are missing;
- configured role node ids do not match connected inputs;
- join keys are invalid or missing;
- Polars raises a schema/type error.

### Undo And Redo

Creating an edge-join is one logical operation:

- add one node;
- remove one edge;
- add three edges;
- select the new node.

Undo should restore the exact previous graph, including edge handles and
selection state where existing undo infrastructure preserves it.

## Tests

Follow TDD for implementation. Start each implementation slice with failing
tests, then implement until they pass.

### Backend Tests

- `NodeType` enum includes `EDGE_JOIN` and serialises to `"edgeJoin"`.
- registry completeness fails if edge-join lacks exec or codegen registration.
- exec builder joins two lazy frames with same-name keys.
- exec builder joins with `leftOn`/`rightOn`.
- exec builder supports configured join type and suffix.
- exec builder rejects missing role config.
- exec builder rejects stale role ids that are not connected.
- exec builder rejects ambiguous duplicate role inputs.
- exec builder rejects invalid key combinations.
- codegen emits `@pipeline.edge_join(...)`.
- parser round-trips generated edge-join code.
- save/load round-trip preserves edge handles across the split-edge topology.
- preview returns node-level errors for disconnected draft edge-joins.
- deploy pruning treats edge-join as a normal transform ancestor.
- trace includes edge-join as a normal trace step.
- config validation warns or rejects unknown edge-join keys consistently with
  the rest of the codebase.

### Frontend Tests

- `edgeJoin` is registered in node metadata but excluded from the palette.
- compact node renders stable handles `base`, `join`, and source.
- creating from an edge drop rewrites `A -> B` plus `C` into `A -> J -> B` and
  `C -> J`.
- rewrite preserves original `sourceHandle` and `targetHandle`.
- rewrite preserves dragged `sourceHandle`.
- rewrite refuses to create a cycle.
- repeated joins can be inserted into already split segments.
- undo restores the original edge and removes the join node.
- NodePanel renders the edge-join editor.
- editor updates join config and clears cached preview shape.
- invalid input topology renders an actionable panel error.
- preview selection follows the existing selected-node behaviour.

### E2E Tests

- User can create an edge-join by dragging onto an edge, configure keys, save,
  reload, and preview the joined dataframe.
- User can create two joins on the same branch and both persist.
- User can trace through an output that depends on an edge-join and see the
  join node highlighted.

## Edge Cases

### Dropping Onto An Edge From Its Own Upstream Branch

If the dragged source is already downstream of the target edge in a way that
would create a cycle, reject before mutating graph state.

### Existing Edge Has Handles

Preserve handles exactly:

- old `sourceHandle` stays with the upstream-to-join segment;
- old `targetHandle` stays with the join-to-downstream segment.

This matters for API-input ports and submodel boundary edges.

### Multi-Port Sources

If the dragged source is multi-port, the new join-input edge must carry the
selected `sourceHandle`. A null handle from a multi-port source should continue
to fail through existing executor validation.

### Renaming Source Nodes

Config roles store node ids. Renaming labels must not break execution. Codegen
will emit function names, and parser will reconstruct ids from function names
on reload.

### Rewiring Inputs Manually

If a user manually reconnects the `base` or `join` handle, the editor should
update the matching role id. If an edge is connected without the required target
handle, validation should fail loudly.

### Cross Joins

`how: "cross"` is allowed only with no join keys. The UI should make this
mutual exclusion obvious.

### Duplicate Columns

The default suffix is `_right`. If Polars raises because duplicate columns
remain unresolved, surface the Polars error rather than inventing a fallback.

### Empty Join Key Lists

Empty `on`, `leftOn`, or `rightOn` means not configured. Non-cross joins require
configured keys.

## Implementation Plan

Each functionality item below should be implemented with a developer/reviewer
agent pair when implementation starts, following the repository instruction in
`AGENTS.md`. For this spec-only change, no implementation agents are required.

1. Backend node contract
   - Add `NodeType.EDGE_JOIN`, decorator mappings, pipeline decorator, config
     keys, registry entries, and backend contract tests.
   - Keep the builder opaque for column-contract purposes in v1.

2. Backend execution and codegen
   - Build the join executor and structured codegen/parser round-trip.
   - Ensure parameter order and role resolution are deterministic and fail
     loudly when stale.

3. Frontend node metadata and compact rendering
   - Register `edgeJoin` outside the palette.
   - Render a compact node with stable role handles and normal status/trace
     states.

4. Edge-drop graph rewrite
   - Implement edge targeting and the `A -> J -> B`, `C -> J` rewrite.
   - Preserve handles, prevent cycles, and make the operation undoable as one
     user action.

5. Edge-join editor
   - Add a focused join editor for join type, key mapping, suffix, and advanced
     join options.
   - Reuse existing upstream column discovery and panel patterns.

6. Integration and end-to-end coverage
   - Verify save/load, preview, trace, repeated joins, API-input ports, submodel
     boundary handles, and deployment pruning.

## Non-Goals For V1

- Palette-created edge-join nodes.
- Arbitrary custom code inside the edge-join editor.
- Automatic key inference that silently configures joins without user approval.
- Hidden edge semantics or execution directly attached to edges.
- Join visualisation beyond the compact node itself.
- Multi-output joins.
- Expression-based join keys. V1 join keys are column names only.

## Resolved V1 Decisions

- Deleting an input removes the relevant incoming edge and leaves the node in a
  loud invalid state until it is reconnected.
- Compact rendering is driven by node metadata, with edge-join-specific handle
  placement where the role geometry differs from normal nodes.
- Advanced Polars options are exposed in the editor because they map directly
  to `DataFrame.join` / `LazyFrame.join` keyword arguments.
- Edge targeting is handled through the existing connection-end interaction and
  tested graph rewrite utilities rather than a second graph persistence model.
