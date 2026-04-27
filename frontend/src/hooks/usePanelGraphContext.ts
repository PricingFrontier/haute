import { useMemo } from "react"
import type { Edge, Node } from "@xyflow/react"
import type { SimpleEdge, SimpleNode } from "../panels/NodePanel"
import useGraphStore from "../stores/useGraphStore"
import { nodeData } from "../types/node"

export interface PanelGraphContextSnapshot {
  allNodes: SimpleNode[]
  edges: SimpleEdge[]
  nodeById: Map<string, SimpleNode>
  getNode: (id: string | null | undefined) => SimpleNode | null
}

export function toSimpleNode(node: Node): SimpleNode {
  const data = nodeData(node)
  return {
    id: node.id,
    type: node.type,
    data: {
      ...node.data,
      label: data.label || node.id,
      description: data.description ?? "",
      nodeType: data.nodeType || node.type || "",
      config: data.config,
    },
  }
}

export function toSimpleEdge(edge: Edge): SimpleEdge {
  return { id: edge.id, source: edge.source, target: edge.target }
}

function buildSnapshot(nodes: Node[], edges: Edge[]): PanelGraphContextSnapshot {
  const allNodes = nodes.map(toSimpleNode)
  const simpleEdges = edges.map(toSimpleEdge)
  const nodeById = new Map(allNodes.map((node) => [node.id, node]))
  return {
    allNodes,
    edges: simpleEdges,
    nodeById,
    getNode: (id) => (id ? nodeById.get(id) ?? null : null),
  }
}

function buildSnapshotForVersion(panelContextVersion: number): PanelGraphContextSnapshot {
  void panelContextVersion
  const { nodes, edges } = useGraphStore.getState()
  return buildSnapshot(nodes, edges)
}

export default function usePanelGraphContext(): PanelGraphContextSnapshot {
  const panelContextVersion = useGraphStore((s) => s.panelContextVersion)
  return useMemo(
    () => buildSnapshotForVersion(panelContextVersion),
    // Intentionally keyed to the panel context version. Position-only node
    // churn updates persisted layout/React Flow state, but it does not change
    // the selected-node editor context. Preview columns and schema warnings do.
    [panelContextVersion],
  )
}
