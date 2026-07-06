/**
 * V056 — standalone simulation of the downstream-cascade logic in
 * frontend/src/hooks/usePipelineAPI.ts (fetchPreviewImmediate -> propagate ->
 * drainReadyQueue), reproduced faithfully to demonstrate the submodel-specific
 * dead-cascade.
 *
 * This is NOT the React hook; it is a minimal extraction of the exact control
 * flow so the lookup-miss can be executed and asserted. It mirrors:
 *   - line 318-319: cascadeNodes = resolveGraphFromRefs(...).nodes  (PARENT nodes when drilled in)
 *   - line 332:     const { edges } = graphRef.current              (SUBMODEL edges when drilled in)
 *   - line 337-353: BFS over `edges` -> reachableNodeIds
 *   - line 439-443: dsNode = cascadeNodes.find(n => n.id === nodeId); if (!dsNode) settle without preview
 *
 * Scenario: drilled into a submodel. Submodel canvas has data nodes A -> B.
 * The user changes A's columns. The cascade should re-preview B. We assert it
 * does NOT (the bug), because B's id is absent from the parent node set used
 * for the lookup.
 */

import assert from "node:assert/strict"

// canPreviewNode equivalent: data nodes are previewable.
function canPreviewNode(node) {
  const NON = new Set(["submodel", "submodel-port"])
  return !NON.has(node.type)
}

// resolveGraphFromRefs (buildGraph.ts:31-33): parent takes priority.
function resolveGraphFromRefs(graphRef, parentGraphRef) {
  return parentGraphRef.current
    ? { nodes: parentGraphRef.current.nodes, edges: parentGraphRef.current.edges }
    : { nodes: graphRef.current.nodes, edges: graphRef.current.edges }
}

/**
 * Faithful extraction of propagate + drainReadyQueue. `previewedNodeIds`
 * collects every node id for which a real downstream preview would have been
 * issued (i.e. reached the `previewNode({...})` call at line 446). The bug is
 * that B is settled at line 441 BEFORE that call, so it never appears here.
 */
function runCascade({ graphRef, parentGraphRef, changedNodeId }) {
  // line 318-319
  const graph = resolveGraphFromRefs(graphRef, parentGraphRef)
  const cascadeNodes = graph.nodes

  // line 332 — edges always come from graphRef.current (live canvas = submodel when drilled in)
  const { edges } = graphRef.current

  const childrenBySource = new Map()
  const reachableNodeIds = new Set()
  const queue = [changedNodeId]
  for (const edge of edges) {
    const children = childrenBySource.get(edge.source)
    if (children) children.push(edge.target)
    else childrenBySource.set(edge.source, [edge.target])
  }
  for (let i = 0; i < queue.length; i++) {
    const sourceId = queue[i]
    for (const targetId of childrenBySource.get(sourceId) ?? []) {
      if (reachableNodeIds.has(targetId)) continue
      reachableNodeIds.add(targetId)
      queue.push(targetId)
    }
  }

  const previewedNodeIds = []   // nodes that reach the real previewNode() call
  const settledWithoutPreview = [] // nodes short-circuited at line 441

  // Simplified drain: every reachable node becomes "ready" (its single parent
  // settled with columnsChanged=true). We then run the line 439-443 gate.
  for (const nodeId of reachableNodeIds) {
    // line 439
    const dsNode = cascadeNodes.find((n) => n.id === nodeId)
    // line 440-443
    if (!dsNode || !canPreviewNode(dsNode)) {
      settledWithoutPreview.push(nodeId)
      continue
    }
    // line 446 onward — this is where _columns / requestedPreviewColumns refresh
    previewedNodeIds.push(nodeId)
  }

  return { reachableNodeIds: [...reachableNodeIds], previewedNodeIds, settledWithoutPreview }
}

// ---- Build synthetic graphs ----

// Submodel canvas: data nodes A -> B (both previewable). This is what
// setNodesRaw/setEdgesRaw loaded into graphRef while drilled in.
const submodelNodes = [
  { id: "n_A", type: "transform", data: {} },
  { id: "n_B", type: "transform", data: {} },
]
const submodelEdges = [
  { id: "e_A_B", source: "n_A", target: "n_B" },
]

// Parent canvas: a single submodel node + an unrelated data node. The
// submodel's internal nodes (n_A, n_B) are NOT present here.
const parentNodes = [
  { id: "submodel__mysm", type: "submodel", data: {} },
  { id: "n_parent", type: "transform", data: {} },
]
const parentEdges = [
  { id: "e_parent_sm", source: "n_parent", target: "submodel__mysm" },
]

const graphRef = { current: { nodes: submodelNodes, edges: submodelEdges } }
const parentGraphRef = { current: { nodes: parentNodes, edges: parentEdges } }

// ---- DRILLED-IN case (the bug) ----
const drilled = runCascade({ graphRef, parentGraphRef, changedNodeId: "n_A" })
console.log("[drilled-in] reachable:", drilled.reachableNodeIds)
console.log("[drilled-in] previewed:", drilled.previewedNodeIds)
console.log("[drilled-in] settledWithoutPreview:", drilled.settledWithoutPreview)

// The cascade DID reach n_B (BFS over submodel edges finds it)...
assert.deepEqual(drilled.reachableNodeIds, ["n_B"], "BFS over submodel edges must reach n_B")
// ...but it was settled WITHOUT a preview (the silent dead cascade):
assert.deepEqual(
  drilled.previewedNodeIds,
  [],
  "BUG: downstream submodel node n_B was never re-previewed",
)
assert.deepEqual(
  drilled.settledWithoutPreview,
  ["n_B"],
  "BUG: n_B short-circuited at the !dsNode gate because cascadeNodes holds PARENT nodes",
)

// ---- TOP-LEVEL control case (parentGraphRef null) ----
// Same submodel graph, but now treated as the top-level canvas: cascadeNodes
// == graphRef nodes, so the lookup succeeds and n_B IS previewed.
const topGraphRef = { current: { nodes: submodelNodes, edges: submodelEdges } }
const topParentRef = { current: null }
const top = runCascade({ graphRef: topGraphRef, parentGraphRef: topParentRef, changedNodeId: "n_A" })
console.log("[top-level] previewed:", top.previewedNodeIds)
assert.deepEqual(
  top.previewedNodeIds,
  ["n_B"],
  "CONTROL: at top level the same downstream node IS re-previewed",
)

console.log("\nV056 REPRODUCED: cascade walks submodel edges but resolves nodes")
console.log("from the parent graph -> downstream submodel children are settled")
console.log("without re-preview (dead cascade). Top-level control path is correct.")
