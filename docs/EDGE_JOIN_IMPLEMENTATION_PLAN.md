# Edge-Join Implementation Plan

Status: implemented in the current branch; retained as the rollout record
Owner: Graph editing / dataframe transforms workstream
Last updated: 2026-06-09

## Goal

Implement the `edge-join` node described in `docs/EDGE_JOIN_NODE_SPEC.md` as a
real Haute node type that behaves like every other node in execution, preview,
trace, save/load, parser, codegen, deployment, and UI panel workflows.

The only special behaviours are:

- creation: the node is created by dropping a connection onto an existing edge;
- canvas shape: the node renders smaller than normal pipeline nodes.

Everything else should use existing graph, config, execution, and panel
patterns. The feature should not introduce hidden edge semantics, broad
fallbacks, or a second graph persistence model.

## Engineering Principles

- Start every slice with failing tests.
- Keep `edgeJoin` a first-class node type, not an edge annotation.
- Preserve existing edge metadata exactly when rewriting graph topology.
- Fail loudly on stale config, invalid role handles, missing join keys, missing
  columns, duplicate role bindings, and cycle creation.
- Use structured helpers and typed config validation rather than ad hoc string
  manipulation.
- Keep the UI compact but accessible: visible target, stable dimensions,
  keyboard-compatible node selection, and normal trace/status states.
- Prefer small, pure graph-rewrite utilities that are easy to test outside
  React Flow.
- Do not add arbitrary custom-code fallback inside edge-join. Users can place a
  normal Polars node before or after an edge-join for bespoke logic.

## Team Model

Each implementation slice uses two agents:

- Developer agent: writes failing tests first, then implements the slice.
- Reviewer agent: reviews the slice for correctness, consistency,
  maintainability, UX, performance, and unnecessary fallback behaviour.

The main thread integrates both outputs, runs the relevant checks, resolves
review findings, and only then moves to the next slice.

## Slice 0: Preflight Contracts And Current Gaps

Agent pair:

- Developer agent: backend contracts developer.
- Reviewer agent: backend contracts reviewer.

Goal: make the existing edge-handle contract explicit before adding edge-join.

Why first: edge-join depends on preserving `sourceHandle` and `targetHandle`
through graph rewrites. Codegen/parser already know about `source_port`, but the
public pipeline API must also accept the generated form consistently.

Tests first:

- `Pipeline.connect("a", "b", source_port="p")` is accepted and appears in
  `Pipeline.to_graph()` as `sourceHandle: "p"`.
- `Pipeline.connect("a", "b")` still produces a null `sourceHandle`.
- Existing `tests/test_commit6_port_aware_edges.py` continues to pass.
- Empty source ports fail loudly rather than becoming null.
- GraphEdge target/source handle validation remains unchanged.

Implementation scope:

- `src/haute/pipeline.py`
- existing parser/codegen tests if they expose mismatches
- small focused tests in `tests/test_pipeline.py` or
  `tests/test_commit6_port_aware_edges.py`

Acceptance criteria:

- Generated code that uses `source_port` can be executed through the public
  Pipeline API.
- No change weakens `GraphEdge` handle validation.
- Slice 0 itself does not require any edge-join implementation.

Suggested verification:

```bash
uv run pytest tests/test_commit6_port_aware_edges.py tests/test_pipeline.py
```

## Slice 1: Backend Node-Type Contract

Agent pair:

- Developer agent: node registry developer.
- Reviewer agent: node registry reviewer.

Goal: register `edgeJoin` everywhere the backend expects a real node type,
without implementing full join execution yet.

Tests first:

- `NodeType.EDGE_JOIN.value == "edgeJoin"`.
- `NodeType("edgeJoin") is NodeType.EDGE_JOIN`.
- config validation includes edge-join valid keys.
- registry completeness test fails if edge-join lacks exec or codegen entries.
- chunk capability declaration coverage includes edge-join if the current
  coverage gate requires all node types.
- `@pipeline.edge_join` is recognised by the decorator maps.
- `@pipeline.edge_join(...)` parses into `nodeType == "edgeJoin"` with config
  keys normalised from snake-case decorator kwargs to camel-case graph config.

Implementation scope:

- `src/haute/_types.py`
- `src/haute/pipeline.py`
- `src/haute/_config_validation.py`
- `src/haute/_config_builder.py`
- `src/haute/_builders.py`
- `src/haute/_codegen_builders.py`
- `src/haute/chunking.py` if required by registry/capability tests
- focused backend tests in `tests/test_types.py`,
  `tests/test_config_validation.py`, `tests/test_parser.py`,
  `tests/test_codegen.py`, and registry/contract tests

Design decisions:

- V1 stores edge-join config inline in decorator kwargs, not in a sidecar
  folder. The config is small and closer to `scenarioExpander` than to a
  rating table.
- Add an `EdgeJoinConfig` TypedDict with keys:
  `baseInput`, `joinInput`, `how`, `on`, `leftOn`, `rightOn`, `suffix`,
  `coalesce`, `validate`, and `maintainOrder`.
- Register the exec builder as an intentional loud placeholder until Slice 2,
  or land Slice 1 and Slice 2 together if registry readiness makes a
  placeholder awkward.
- Register column contract as explicitly opaque in v1.

Acceptance criteria:

- Importing `haute.codegen` still validates the unified registry.
- Unknown/stale config keys are surfaced by the existing validation mechanism.
- No parser/codegen fallback treats edge-join as a generic Polars transform.

Suggested verification:

```bash
uv run pytest tests/test_types.py tests/test_config_validation.py tests/test_parser.py tests/test_codegen.py tests/test_registry_contracts.py
```

## Slice 2: Edge-Join Execution Semantics

Agent pair:

- Developer agent: executor developer.
- Reviewer agent: executor reviewer.

Goal: execute an edge-join as a deterministic two-input Polars lazy join with
strict role and key validation.

Tests first:

- same-name key left join returns expected rows and columns.
- `leftOn`/`rightOn` join returns expected rows and columns.
- `how` is passed through for `left`, `inner`, `full`, `semi`, `anti`, and
  `cross` where supported by the installed Polars version.
- suffix is applied to duplicate right-side column names.
- `cross` rejects configured keys.
- non-cross joins reject missing keys.
- `on` rejects simultaneous `leftOn` or `rightOn`.
- `leftOn` and `rightOn` length mismatch fails loudly.
- missing `baseInput` or `joinInput` fails before calling Polars.
- stale role id not present in `source_ids` fails loudly.
- duplicate role ids fail loudly.
- target handles `base` and `join` are honoured when source id role config and
  edge handles disagree.
- a multi-port source with missing `sourceHandle` continues to fail through the
  existing multi-port validation.

Implementation scope:

- `src/haute/_builders.py`
- optional new helper module such as `src/haute/_edge_join.py`
- executor tests in `tests/test_executor_builders.py`,
  `tests/test_executor.py`, or a new focused `tests/test_edge_join.py`

Design decisions:

- Resolve input frames by configured `baseInput`/`joinInput` and `source_ids`.
- Also validate incoming edge `targetHandle` values when available. The base
  edge should target `base`; the join edge should target `join`.
- If role config and handles conflict, fail loudly. Do not silently choose one.
- Return `pl.LazyFrame` only; do not collect.
- Keep column contract opaque in v1.

Acceptance criteria:

- Edge-join works in preview/lazy execution as a normal transform ancestor.
- Stale or malformed graph state fails with a typed, actionable error.
- No fallback to "first input joins second input" exists.

Suggested verification:

```bash
uv run pytest tests/test_executor_builders.py tests/test_executor.py tests/test_execute_lazy.py tests/test_edge_join.py
```

## Slice 3: Codegen, Parser, And Save/Load Round Trip

Agent pair:

- Developer agent: codegen/parser developer.
- Reviewer agent: codegen/parser reviewer.

Goal: make Python source remain the source of truth for edge-join nodes.

Tests first:

- codegen emits `@pipeline.edge_join(...)`.
- codegen emits snake-case decorator kwargs while graph config remains
  camel-case.
- generated function body uses `base.join(join, ...)` and does not store
  generated boilerplate as custom code.
- generated function parameters are ordered base first, join second regardless
  of incoming edge declaration order.
- parser round-trips `base_input`, `join_input`, `left_on`, `right_on`,
  `maintain_order`, and other supported kwargs back into graph config.
- parser round-trips same-key joins using `on`.
- graph-to-code-to-parse preserves the split topology and edge handles.
- existing `source_port` connect calls are still preserved.
- generated source remains valid when node names need sanitisation.

Implementation scope:

- `src/haute/_codegen_builders.py`
- `src/haute/codegen.py` if source-name ordering needs role-aware support
- `src/haute/_config_builder.py`
- `src/haute/_code_extraction.py` only if generated join bodies require
  boilerplate stripping
- parser/codegen tests in `tests/test_codegen.py`,
  `tests/test_parser.py`, `tests/test_parser_roundtrip.py`, and
  `tests/test_save_pipeline_integrity.py`

Design decisions:

- Prefer a codegen helper that builds join kwargs from validated config.
- Keep generated Python readable and explicit.
- Do not parse arbitrary custom join code into edge-join config. V1 recognises
  its own generated shape and decorator kwargs only.

Acceptance criteria:

- Saving and reloading an edge-join graph produces the same node type, config,
  edge roles, and handles.
- Codegen does not emit Polars fallback code for edge-join.
- Parser does not lose input roles when labels are renamed and then saved.

Suggested verification:

```bash
uv run pytest tests/test_codegen.py tests/test_parser.py tests/test_parser_roundtrip.py tests/test_save_pipeline_integrity.py
```

## Slice 4: Pure Frontend Graph Rewrite Utility

Agent pair:

- Developer agent: graph rewrite developer.
- Reviewer agent: graph rewrite reviewer.

Goal: implement edge insertion as a pure, tested graph transformation before
wiring it to pointer events.

Tests first:

- rewriting `A -> B` plus dropped `C` creates `A -> J -> B` and `C -> J`.
- original `sourceHandle` moves to `A -> J`.
- original `targetHandle` moves to `J -> B`.
- dropped connection `sourceHandle` moves to `C -> J`.
- `A -> J` uses `targetHandle: "base"`.
- `C -> J` uses `targetHandle: "join"`.
- repeated joins on already split segments work.
- rewrite refuses self joins and cycle creation.
- rewrite refuses missing source/target nodes.
- rewrite refuses target edges that are already invalid or incomplete.
- ids are deterministic enough for tests and unique enough for the live graph.
- the new node config contains `baseInput`, `joinInput`, default `how`, and
  default suffix.

Implementation scope:

- new frontend utility, for example
  `frontend/src/utils/edgeJoinGraph.ts`
- tests under `frontend/src/utils/__tests__/edgeJoinGraph.test.ts`
- possibly `frontend/src/utils/graphHelpers.ts` if cycle helpers already live
  there and can be reused

Design decisions:

- Keep the utility independent from React components and Zustand.
- Pass in the original edge, dropped connection, drop position, and id factory.
- Return `{ nodes, edges, newNodeId }` or a typed failure result.
- Prefer explicit typed failures over `null` so UI can show clear toasts.

Acceptance criteria:

- The entire topology mutation is covered without a browser.
- The utility does not mutate input arrays.
- Cycle prevention is proven before event wiring exists.

Suggested verification:

```bash
cd frontend
npm test -- edgeJoinGraph
```

## Slice 5: Frontend Node Metadata And Compact Rendering

Agent pair:

- Developer agent: compact node developer.
- Reviewer agent: compact node reviewer.

Goal: render edge-join as a normal node with compact dimensions and stable
handles.

Tests first:

- `edgeJoin` exists in `NODE_TYPES` and `NODE_TYPE_META`.
- `edgeJoin` is not present in `PALETTE_TYPES`.
- compact node renders target handles `base` and `join`.
- compact node renders one source handle.
- compact node preserves selected, running, error, trace-active, and
  trace-dimmed visual semantics.
- compact dimensions remain stable across full, medium, and compact zoom modes.
- text does not overflow at expected labels and join-key summaries.

Implementation scope:

- `frontend/src/utils/nodeTypes.ts`
- `frontend/src/nodes/PipelineNode.tsx`
- `frontend/src/nodes/__tests__/PipelineNode.test.tsx`
- `frontend/src/utils/__tests__/nodeTypes.test.ts`
- `frontend/src/App.tsx` node type registry

Design decisions:

- Prefer a metadata-driven `shape: "compact"` or `size: "compact"` if it can
  be kept clean. Use an explicit edge-join branch only if the metadata approach
  makes the normal node path harder to read.
- Reuse transform group colour unless visual testing shows it is unclear.
- Use a lucide icon already available in the dependency set.

Acceptance criteria:

- Edge-join nodes can be loaded from backend graph JSON without unknown-node UI.
- The node is compact but remains accessible and selectable.
- Palette behaviour proves creation is edge-only.

Suggested verification:

```bash
cd frontend
npm test -- nodeTypes PipelineNode
```

## Slice 6: Edge Targeting And Creation Interaction

Agent pair:

- Developer agent: edge interaction developer.
- Reviewer agent: edge interaction reviewer.

Goal: create edge-join nodes through the intended drag-to-edge interaction.

Tests first:

- connection end over a valid edge calls the pure rewrite utility.
- connection end away from any edge does not create a node.
- hover over a valid edge shows a visible insertion target.
- hover leaves clear the insertion target.
- incompatible drops show a toast and do not mutate graph state.
- the rewrite is captured as one undoable operation.
- selected node becomes the new edge-join node.
- existing canvas node creation by palette drop is unchanged.

Implementation scope:

- `frontend/src/hooks/useEdgeHandlers.ts`
- `frontend/src/App.tsx`
- optional custom edge component under `frontend/src/edges/`
- `frontend/src/hooks/__tests__/useEdgeHandlers.test.ts`
- App integration tests where React Flow mocking makes sense

Design decisions:

- Evaluate custom edge type first because it gives a natural wide invisible hit
  area and hover marker.
- If React Flow connection-end events make custom edges too invasive, use
  nearest-edge math in `onConnectEnd`, backed by pure utility tests.
- Keep normal `onConnect` node-to-node behaviour unchanged.
- Never create without visible target feedback.

Acceptance criteria:

- The main interaction works without breaking ordinary edge creation.
- Undo returns to the exact previous graph.
- Edge handles survive the interaction.

Suggested verification:

```bash
cd frontend
npm test -- useEdgeHandlers App.integration
```

## Slice 7: Edge-Join Config Editor

Agent pair:

- Developer agent: editor developer.
- Reviewer agent: editor reviewer.

Goal: provide a focused right-panel editor that matches existing panel
patterns and prevents malformed join configs.

Tests first:

- NodePanel renders the edge-join editor for `nodeType: "edgeJoin"`.
- editor displays base and join input sources by role.
- join type control updates `how`.
- same-key selector updates `on` and clears `leftOn`/`rightOn`.
- asymmetric key rows update `leftOn` and `rightOn` and clear `on`.
- `cross` disables or clears key controls.
- suffix input updates `suffix`.
- advanced controls update `coalesce`, `validate`, and `maintainOrder`.
- stale roles render an actionable validation message.
- deleting an input removes the correct edge and leaves a validation message.
- config edits clear cached preview shape using the existing NodePanel helper.

Implementation scope:

- new editor, for example
  `frontend/src/panels/editors/EdgeJoinEditor.tsx`
- `frontend/src/panels/LazyNodeEditors.tsx`
- `frontend/src/panels/NodePanel.tsx`
- editor tests under `frontend/src/__tests__/editors/EdgeJoinEditor.test.tsx`
- panel tests under `frontend/src/panels/__tests__/NodePanel.test.tsx`

Design decisions:

- Reuse `InputSource` and upstream column collection patterns.
- Keep the UI explicit. Do not auto-infer keys without user action.
- Treat role mismatch as invalid config, not as a chance to guess.
- Hide advanced Polars options behind a small section if they add noise.

Acceptance criteria:

- A user can configure common same-key and asymmetric-key joins without writing
  code.
- Invalid role/key state is obvious before preview.
- Editor state matches backend config exactly.

Suggested verification:

```bash
cd frontend
npm test -- EdgeJoinEditor NodePanel
```

## Slice 8: Backend Integration Surfaces

Agent pair:

- Developer agent: integration developer.
- Reviewer agent: integration reviewer.

Goal: prove edge-join behaves like a normal node across preview, trace,
deployment pruning, config persistence, and cache fingerprints.

Tests first:

- previewing an edge-join returns joined rows.
- previewing downstream of an edge-join observes joined columns.
- previewing malformed edge-join returns a node-level error.
- trace includes the edge-join node as a normal step.
- deploy pruning keeps edge-join when it is an output ancestor.
- deploy pruning drops edge-join when it is not an output ancestor.
- graph fingerprint changes when edge-join config changes.
- graph fingerprint changes when edge-join topology changes.
- config collection does not expect a sidecar folder for edge-join in v1.

Implementation scope:

- likely mostly tests, unless gaps surface
- `tests/test_preview_cache_regressions.py`
- `tests/test_trace_integration.py`
- `tests/test_deploy_internals.py` or deploy-pruner tests
- `tests/test_graph_fingerprint_cached.py`
- `tests/test_config_io.py`

Design decisions:

- Let the existing graph and cache machinery do the work.
- Add integration tests where behaviour matters; avoid adding special-case code
  unless a test proves an existing path cannot handle the node.

Acceptance criteria:

- Edge-join has no bespoke preview/trace/deploy bypass.
- The feature behaves like a normal transform in every backend surface.

Suggested verification:

```bash
uv run pytest tests/test_edge_join.py tests/test_preview_cache_regressions.py tests/test_trace_integration.py tests/test_deploy_internals.py tests/test_graph_fingerprint_cached.py tests/test_config_io.py
```

## Slice 9: End-To-End Browser Coverage

Agent pair:

- Developer agent: e2e developer.
- Reviewer agent: e2e reviewer.

Goal: prove the user workflow works in the real editor.

Tests first:

- create an edge-join by dragging a connection onto an existing edge.
- configure same-name join keys.
- preview the edge-join and see joined columns.
- save, reload, and confirm node/config/topology remain.
- create two edge-joins on the same branch.
- trace a downstream output and confirm edge-join highlight/step.
- verify API-input source handles survive edge-join insertion.

Implementation scope:

- `frontend/e2e/core-flows.spec.ts` or a new focused e2e spec
- test fixtures under `tests/fixtures` or frontend e2e helpers if needed
- browser screenshot/assertion coverage for compact node target visibility

Design decisions:

- Keep e2e data small and deterministic.
- Use lower-level frontend unit tests for edge math; e2e should cover the
  workflow, not every geometry edge case.
- Add visual assertions only where they guard the key UX: visible edge target
  and compact node presence.

Acceptance criteria:

- A user can complete the intended workflow without touching code.
- Save/reload proves Python source remains the source of truth.
- Repeated joins are not a one-off special case.

Suggested verification:

```bash
cd frontend
npm run test:e2e -- --grep "edge join"
```

## Slice 10: Hardening, Documentation, And Release Gates

Agent pair:

- Developer agent: hardening developer.
- Reviewer agent: hardening reviewer.

Goal: leave the codebase easy to maintain after the feature lands.

Tests and checks:

- full backend focused suite from slices 0 through 8.
- frontend unit tests for metadata, node rendering, graph rewrite, hooks,
  editor, and panel integration.
- e2e edge-join workflow.
- `uv run pytest tests/test_registry_contracts.py tests/test_config_validation.py`
  to catch future node-type drift.
- frontend dependency audit and bundle budget remain green.
- no direct string-built graph rewrites in components.
- no route or executor hidden fallback was added for edge-join.

Documentation:

- Link `docs/EDGE_JOIN_NODE_SPEC.md` and this plan from relevant node docs when
  implementation is complete.
- Add user-facing docs under `docs/building-models/nodes/edge-join.md`.
- Mention that arbitrary code belongs in normal Polars nodes.
- Document how same-key, asymmetric-key, and cross joins behave.

Acceptance criteria:

- All slice acceptance criteria are met.
- No TODOs or temporary feature flags remain in production paths.
- The final PR description includes test evidence by slice.
- Reviewer agent signs off that no unnecessary fallbacks were added.

## Cross-Slice Test Matrix

Backend:

- node enum, parser, codegen, config validation, registry completeness
- public `Pipeline.connect` source-port compatibility
- exec builder role validation
- same-key, asymmetric-key, cross, and suffix joins
- save/load handle preservation
- preview, trace, deploy pruning, graph fingerprint, config collection

Frontend:

- metadata and palette exclusion
- compact node rendering and handles
- pure graph rewrite
- edge hover/drop interaction
- undo/redo
- editor validation and config updates
- App integration and e2e workflow

Robustness:

- stale role ids
- missing handles
- duplicate role bindings
- cycle creation
- multi-port source handles
- submodel boundary handles
- renamed labels
- repeated joins
- invalid Polars schemas

## Suggested Rollout Order

1. Slice 0: preflight `source_port` contract.
2. Slice 1: backend node-type contract.
3. Slice 2: backend execution semantics.
4. Slice 3: codegen/parser/save-load.
5. Slice 4: pure frontend graph rewrite.
6. Slice 5: metadata and compact rendering.
7. Slice 6: edge targeting and creation interaction.
8. Slice 7: config editor.
9. Slice 8: backend integration surfaces.
10. Slice 9: end-to-end browser coverage.
11. Slice 10: hardening and docs.

This order keeps the source-of-truth and execution contracts stable before the
UI can create a persisted edge-join. It also makes the riskiest parts of the
feature, handle preservation and graph rewrites, independently testable before
React Flow pointer behaviour is involved.

## Stop Conditions

Pause implementation and reassess if any of these appear:

- React Flow cannot reliably expose connection-end pointer coordinates or edge
  hit targeting without a brittle DOM query.
- parser/codegen cannot preserve role ordering without a broader codegen API
  change.
- Polars join option names differ across the supported version range in a way
  that would require runtime fallbacks.
- submodel boundary handle preservation conflicts with existing flattening
  assumptions.

When a stop condition is hit, add a failing test that demonstrates the conflict,
then decide whether to narrow v1 scope or make the required foundation change.
