/**
 * Pipeline graph diff — which components changed between two versions (S11).
 *
 * Backs the read-only comparison view: the historical version (left) and the
 * current version (right) are diffed by node so changed components can be
 * highlighted. Pure; no store reads.
 */
import type { Node } from "@xyflow/react"

import { canonicalize } from "./graphSnapshot"

export interface GraphDiff {
  /** Node ids present in the NEW graph but not the OLD (highlight on the right). */
  added: Set<string>
  /** Node ids present in the OLD graph but not the NEW (highlight on the left). */
  removed: Set<string>
  /** Node ids in both whose semantic content differs (highlight on both sides). */
  changed: Set<string>
}

/**
 * Semantic content of a node for diffing — its type, label, and config only.
 * Deliberately excludes position (a move is not a change), React-Flow UI fields,
 * and runtime fields (``_status`` / ``_trace…``), which live outside these keys.
 * Key order is canonicalised so equal content compares equal regardless of how
 * the object was constructed.
 */
function nodeContent(n: Node): string {
  const data = (n.data ?? {}) as Record<string, unknown>
  return JSON.stringify(
    canonicalize({ nodeType: data.nodeType, label: data.label, config: data.config }),
  )
}

/**
 * Diff two pipeline graphs by node: which components were added (in NEW only),
 * removed (in OLD only), or changed (same id, different semantic content).
 * Matching is by node id, which is stable across commits while the underlying
 * function name is; a renamed node surfaces as one removed + one added.
 */
export function diffPipelineNodes(
  oldNodes: readonly Node[],
  newNodes: readonly Node[],
): GraphDiff {
  const oldById = new Map(oldNodes.map((n) => [n.id, n]))
  const newById = new Map(newNodes.map((n) => [n.id, n]))
  const added = new Set<string>()
  const removed = new Set<string>()
  const changed = new Set<string>()

  for (const id of newById.keys()) {
    if (!oldById.has(id)) added.add(id)
  }
  for (const [id, oldNode] of oldById) {
    const newNode = newById.get(id)
    if (!newNode) {
      removed.add(id)
      continue
    }
    if (nodeContent(oldNode) !== nodeContent(newNode)) changed.add(id)
  }
  return { added, removed, changed }
}
