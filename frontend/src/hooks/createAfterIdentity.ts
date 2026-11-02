/**
 * The one way a canvas creation resolves server-owned identities before it
 * commits: Duplicate, Create Instance, palette and connection drops, Edge Join
 * insertion and paste.
 */
import type { Edge, Node } from "@xyflow/react"

import { apiErrorMessage } from "../api/errors"
import { structuralFingerprint } from "../utils/structuralFingerprint"

type Graph = { nodes: Node[]; edges: Edge[] }

/** A node's data without its underscore-prefixed fields: what an analyst authored. */
export function authoredNodeData(data: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(data).filter(([key]) => !key.startsWith("_")))
}

function sameAuthoredData(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const authoredA = authoredNodeData(a)
  const authoredB = authoredNodeData(b)
  const keys = Object.keys(authoredA)
  return keys.length === Object.keys(authoredB).length
    && keys.every((key) => Object.hasOwn(authoredB, key) && Object.is(authoredA[key], authoredB[key]))
}

export interface CreateAfterIdentityArgs<T> {
  graphRef: { readonly current: Graph }
  /** One serial per kind of creation: a newer request supersedes a pending one. */
  serialRef: { current: number }
  /** Nodes the creation was computed from; it is refused if one is edited meanwhile. */
  dependsOnNodeIds?: readonly string[]
  resolve: () => Promise<T>
  /** Commit the resolved creation onto the graph as it is now. */
  apply: (resolved: T, graph: Graph) => void
  addToast: (type: "error", text: string) => void
  /** What was refused, as the toast names it: "Node creation", "Paste". */
  action: string
  /** The prefix of a failure's toast: "Create node failed". */
  failure: string
}

/**
 * Resolve, then apply unless a newer request of the kind started, the graph's
 * structure changed, or a node the creation depends on was edited. A selection,
 * drag, measurement or preview stash changes none of these, so it never refuses
 * a creation; `apply` reads the live graph, so such a change is kept.
 */
export async function createAfterIdentity<T>({
  graphRef,
  serialRef,
  dependsOnNodeIds = [],
  resolve,
  apply,
  addToast,
  action,
  failure,
}: CreateAfterIdentityArgs<T>): Promise<void> {
  const captured = graphRef.current
  const structure = structuralFingerprint(captured)
  const dependencies = dependsOnNodeIds.map((id) => captured.nodes.find((node) => node.id === id))
  const serial = ++serialRef.current
  const stillCurrent = (): boolean => {
    if (serialRef.current !== serial) return false
    const graph = graphRef.current
    if (graph === captured) return true
    return structuralFingerprint(graph) === structure && dependencies.every((before, index) => {
      const now = graph.nodes.find((node) => node.id === dependsOnNodeIds[index])
      return before !== undefined && now !== undefined && sameAuthoredData(before.data, now.data)
    })
  }
  const refuse = () => addToast(
    "error",
    `${action} was not applied because the graph changed while identity resolution was running.`,
  )
  try {
    const resolved = await resolve()
    if (!stillCurrent()) {
      refuse()
      return
    }
    apply(resolved, graphRef.current)
  } catch (err: unknown) {
    if (!stillCurrent()) {
      refuse()
      return
    }
    addToast("error", `${failure}: ${apiErrorMessage(err)}`)
  }
}
