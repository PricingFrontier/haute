import { isSubmodelDefinition } from "../types/node"

/**
 * Identity-independent description of a graph's structure.
 *
 * Only what a submodel-boundary edit depends on is serialised: which nodes
 * exist and what kind they are, how they are wired, and the public interface
 * of every submodel definition. Positions, selection, dragging, measured
 * dimensions and all other node data are deliberately excluded, so moving or
 * selecting a node during an in-flight identity resolution does not read as a
 * workspace change.
 */

type FingerprintNode = {
  id: string
  type?: string
  data?: { nodeType?: unknown } | null
}

type FingerprintEdge = {
  id: string
  source: string
  sourceHandle?: string | null
  target: string
  targetHandle?: string | null
}

export type StructuralFingerprintInput = {
  nodes?: readonly FingerprintNode[]
  edges?: readonly FingerprintEdge[]
  submodels?: Record<string, unknown>
}

const text = (value: unknown): string => (typeof value === "string" ? value : "")

export function structuralFingerprint(graph: StructuralFingerprintInput | null | undefined): string {
  if (!graph) return "null"
  const nodes = (graph.nodes ?? [])
    .map((node) => [node.id, text(node.type), text(node.data?.nodeType)])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  const edges = (graph.edges ?? [])
    .map((edge) => [
      edge.id,
      edge.source,
      text(edge.sourceHandle),
      edge.target,
      text(edge.targetHandle),
    ])
    .sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0))
  const submodels = Object.entries(graph.submodels ?? {})
    .map(([definitionId, definition]) => {
      if (!isSubmodelDefinition(definition)) return [definitionId, null]
      const ports = (
        list: readonly { name: string }[],
      ) =>
        list
          .map((port) => port.name)
          .sort((a, b) => (a < b ? -1 : a > b ? 1 : 0))
      return [
        definitionId,
        definition.definitionId,
        ports(definition.inputPorts),
        ports(definition.outputPorts),
      ]
    })
    .sort((a, b) => (String(a[0]) < String(b[0]) ? -1 : String(a[0]) > String(b[0]) ? 1 : 0))
  return JSON.stringify({ nodes, edges, submodels })
}

export default structuralFingerprint
