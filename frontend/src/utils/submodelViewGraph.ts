import type { Edge, Node } from "@xyflow/react"
import type { PipelineEdge, SubmodelBoundaryEdgeData, SubmodelPortData } from "../types/node"
import { normalizeEdges } from "./graphHelpers"
import { NODE_TYPES } from "./nodeTypes"

export interface SubmodelViewGraphInput {
  submodelName: string
  childNodes: Node[]
  childEdges: Edge[]
  parentNodes: Node[]
  parentEdges: Edge[]
}

export interface SubmodelViewGraph {
  nodes: Node[]
  edges: Edge[]
}

type BoundaryData = SubmodelPortData & { nodeType: typeof NODE_TYPES.SUBMODEL_PORT }
const hasText = (value: unknown): value is string => typeof value === "string" && value.length > 0
function stableId(kind: string, values: readonly (string | null)[]): string { return `submodel-view__${kind}__${encodeURIComponent(JSON.stringify(values))}` }
function uniqueEdgeId(base: string, occupied: Set<string>): string { let candidate = base; let suffix = 1; while (occupied.has(candidate)) candidate = `${base}__${suffix++}`; occupied.add(candidate); return candidate }
function boundaryNode(id: string, direction: "input" | "output", ports: SubmodelPortData["ports"], externalNodeIds: string[]): Node<BoundaryData> {
  return { id, type: NODE_TYPES.SUBMODEL_PORT, position: { x: 0, y: 0 }, data: { label: direction === "input" ? "INPUT" : "OUTPUT", nodeType: NODE_TYPES.SUBMODEL_PORT, portDirection: direction, ports, externalNodeIds } }
}

export function buildSubmodelViewGraph({ submodelName, childNodes, childEdges, parentNodes, parentEdges }: SubmodelViewGraphInput): SubmodelViewGraph {
  const childIds = new Set(childNodes.map(node => node.id))
  const inputId = stableId("boundary", [submodelName, "input"])
  const outputId = stableId("boundary", [submodelName, "output"])
  if (childIds.has(inputId) || childIds.has(outputId)) throw new Error(`Submodel boundary id collides with a child node for ${submodelName}`)
  const placeholderId = `submodel__${submodelName}`
  const parentById = new Map(parentNodes.map(node => [node.id, node]))
  const inputPorts: SubmodelPortData["ports"] = []
  const inputExternalIds: string[] = []
  const outputExternalIds: string[] = []
  const inputRows = new Map<string, string>()
  const syntheticEdges: Edge[] = []
  const occupiedEdgeIds = new Set(childEdges.map(edge => edge.id))

  for (const rawEdge of parentEdges) {
    const edge = rawEdge as PipelineEdge
    if (edge.target === placeholderId) {
      const childId = edge.targetHandle === null || edge.targetHandle === undefined ? null : edge.targetHandle.startsWith("in__") ? edge.targetHandle.slice("in__".length) : ""
      if (childId !== null && (!hasText(childId) || !childIds.has(childId))) continue
      if (!inputExternalIds.includes(edge.source)) inputExternalIds.push(edge.source)
      const frameKey = JSON.stringify([edge.source, edge.sourceHandle ?? null])
      let rowId = inputRows.get(frameKey)
      if (!rowId) {
        rowId = stableId("input-row", [edge.source, edge.sourceHandle ?? null])
        inputRows.set(frameKey, rowId)
        const parentLabel = parentById.get(edge.source)?.data.label
        inputPorts.push({ id: rowId, label: hasText(edge.sourceHandle) ? edge.sourceHandle : hasText(parentLabel) ? parentLabel : edge.source, parentEdges: [] })
      }
      const port = inputPorts.find(candidate => candidate.id === rowId)
      if (!port) throw new Error(`Missing input port ${rowId}`)
      port.parentEdges = [...(port.parentEdges ?? []), edge]
      if (childId === null) continue
      syntheticEdges.push({ id: uniqueEdgeId(stableId("input-edge", [rowId, childId, edge.id]), occupiedEdgeIds), source: inputId, sourceHandle: rowId, target: childId, targetHandle: edge.targetPort ?? null, data: { submodelBoundary: { direction: "input", parentEdge: edge } } satisfies SubmodelBoundaryEdgeData })
      continue
    }
    if (edge.source === placeholderId && hasText(edge.sourceHandle)) {
      const childId = edge.sourceHandle.startsWith("out__") ? edge.sourceHandle.slice("out__".length) : ""
      if (hasText(childId) && childIds.has(childId) && !outputExternalIds.includes(edge.target)) outputExternalIds.push(edge.target)
    }
  }

  const config = parentById.get(placeholderId)?.data.config as { outputPorts?: unknown } | undefined
  const outputChildren = Array.isArray(config?.outputPorts) ? config.outputPorts.filter((id): id is string => hasText(id) && childIds.has(id)) : []
  for (const childId of outputChildren) {
    const handle = `out__${childId}`
    const consumers = parentEdges.filter((raw): raw is PipelineEdge => { const edge = raw as PipelineEdge; return edge.source === placeholderId && edge.sourceHandle === handle })
    syntheticEdges.push({ id: uniqueEdgeId(stableId("output-edge", [childId, handle]), occupiedEdgeIds), source: childId, sourceHandle: consumers[0]?.sourcePort ?? null, target: outputId, targetHandle: null, data: { submodelBoundary: { direction: "output", parentConsumerEdges: consumers } } satisfies SubmodelBoundaryEdgeData })
  }
  return { nodes: [...childNodes, boundaryNode(inputId, "input", inputPorts, inputExternalIds), boundaryNode(outputId, "output", [], outputExternalIds)], edges: normalizeEdges([...childEdges, ...syntheticEdges]) }
}
