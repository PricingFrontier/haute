/**
 * Which stepped nodes a save names as edited (CGEN-11, specs/codegen).
 *
 * A stepped node's code is its program and its steps a view of it. A step list held by
 * the browser has lost what JavaScript cannot hold (`1.0` is `1`), so the server writes a
 * node's code from its steps only for the nodes a save names: those whose steps differ
 * from the saved baseline's, or that the baseline does not hold. Every other stepped node
 * is sent with the code the baseline saved for those steps, never the live node's own
 * copy, which is a cache that Undo and Redo restore with whatever rendering it held.
 */
import type { Node } from "@xyflow/react"
import { isSubmodelDefinition, type SubmodelDefinition } from "../types/node"
import { canonicalJson } from "./canonicalJson"
import { isSteppedConfig } from "./polarsStepInputs"

/** A node by the graph that holds it: `submodel` null for the pipeline's own nodes. */
export interface SteppedNodeRef {
  submodel: string | null
  node_id: string
}

/** The code a save wrote for a node it named, as the server answers it. */
export interface SavedStepCode extends SteppedNodeRef {
  code: string
}

interface Document {
  nodes: readonly Node[]
  submodels: Record<string, unknown>
}

function nodeConfig(node: Node): Record<string, unknown> | null {
  const data = node.data as { nodeType?: unknown; config?: unknown } | undefined
  return data !== undefined && isSteppedConfig(String(data.nodeType), data.config)
    ? (data.config as Record<string, unknown>)
    : null
}

function definitionNodes(submodels: Record<string, unknown>, id: string): readonly Node[] | null {
  const definition = submodels[id]
  return isSubmodelDefinition(definition, id) ? (definition.graph.nodes as Node[]) : null
}

/** The baseline's stepped config for the node `nodeId` of the graph `submodel` names. */
export function savedSteppedConfig(
  baseline: Document | null,
  submodel: string | null,
  nodeId: string,
): Record<string, unknown> | null {
  if (baseline === null) return null
  const nodes = submodel === null ? baseline.nodes : definitionNodes(baseline.submodels, submodel)
  const node = nodes?.find((candidate) => candidate.id === nodeId)
  return node === undefined ? null : nodeConfig(node)
}

/**
 * The code the baseline saved for `steps` on that node, or null when the baseline does not
 * hold the node or holds other steps for it: the steps were edited.
 */
export function savedCodeForSteps(
  baseline: Document | null,
  submodel: string | null,
  nodeId: string,
  steps: unknown,
): string | null {
  const saved = savedSteppedConfig(baseline, submodel, nodeId)
  if (saved === null || canonicalJson(saved.steps) !== canonicalJson(steps)) return null
  return typeof saved.code === "string" ? saved.code : ""
}

/**
 * `document` as a save sends it, and the stepped nodes the save names: every stepped node
 * whose steps are the baseline's carries the baseline's code; every other one is named.
 */
export function withEditedSteps<T extends Document>(
  document: T,
  baseline: Document | null,
): { document: T; edited: SteppedNodeRef[] } {
  const edited: SteppedNodeRef[] = []
  const scoped = (submodel: string | null, nodes: readonly Node[]): Node[] =>
    nodes.map((node) => {
      const config = nodeConfig(node)
      if (config === null) return node
      const code = savedCodeForSteps(baseline, submodel, node.id, config.steps)
      if (code === null) {
        edited.push({ submodel, node_id: node.id })
        return node
      }
      return config.code === code ? node : { ...node, data: { ...node.data, config: { ...config, code } } }
    })
  const nodes = scoped(null, document.nodes)
  const submodels = Object.fromEntries(
    Object.entries(document.submodels).map(([id, definition]) => {
      if (!isSubmodelDefinition(definition, id)) return [id, definition]
      const graph = { ...definition.graph, nodes: scoped(id, definition.graph.nodes as Node[]) }
      return [id, { ...definition, graph } satisfies SubmodelDefinition]
    }),
  )
  return { document: { ...document, nodes, submodels }, edited }
}

/** `document` with each node in `saved` given the code the save wrote for it. */
export function withSavedStepCode<T extends Document>(document: T, saved: readonly SavedStepCode[]): T {
  if (saved.length === 0) return document
  const code = new Map(saved.map((entry) => [canonicalJson([entry.submodel, entry.node_id]), entry.code]))
  const scoped = (submodel: string | null, nodes: readonly Node[]): Node[] =>
    nodes.map((node) => {
      const written = code.get(canonicalJson([submodel, node.id]))
      const config = nodeConfig(node)
      if (written === undefined || config === null) return node
      return { ...node, data: { ...node.data, config: { ...config, code: written } } }
    })
  const submodels = Object.fromEntries(
    Object.entries(document.submodels).map(([id, definition]) => {
      if (!isSubmodelDefinition(definition, id)) return [id, definition]
      return [id, { ...definition, graph: { ...definition.graph, nodes: scoped(id, definition.graph.nodes as Node[]) } }]
    }),
  )
  return { ...document, nodes: scoped(null, document.nodes), submodels }
}
