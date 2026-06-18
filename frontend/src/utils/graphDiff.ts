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
  /** Node ids in both, unchanged in content but repositioned (highlight both). */
  moved: Set<string>
}

/**
 * Config keys that are DERIVED by codegen rather than authored by the user, and
 * so must not count as a change. The I/O `contract` is the case in point: codegen
 * emits it as the `"opaque"` sentinel (or an inferred `{inputs,outputs}`) and it
 * shifts whenever an *unrelated* node is added — which made every node read as
 * "changed". Mirrors the "not user-editable, don't flag as dirty" rule that
 * graphSnapshot applies to backend round-trip fields.
 */
const DERIVED_CONFIG_KEYS: ReadonlySet<string> = new Set(["contract"])

/** A node's config with derived (non-user-authored) keys stripped. */
function userConfig(config: unknown): unknown {
  if (!config || typeof config !== "object" || Array.isArray(config)) return config
  const out: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(config as Record<string, unknown>)) {
    if (DERIVED_CONFIG_KEYS.has(k)) continue
    out[k] = v
  }
  return out
}

/**
 * Semantic content of a node for diffing — its type, label, and USER config only.
 * Deliberately excludes position (a move is tracked separately), derived config
 * keys, React-Flow UI fields, and runtime fields (``_status`` / ``_trace…``),
 * which live outside these keys. Key order is canonicalised so equal content
 * compares equal regardless of how the object was constructed.
 */
function nodeContent(n: Node): string {
  const data = (n.data ?? {}) as Record<string, unknown>
  return JSON.stringify(
    canonicalize({ nodeType: data.nodeType, label: data.label, config: userConfig(data.config) }),
  )
}

/** Whether a node's canvas position differs between the two graphs (rounded). */
function positionChanged(a: Node, b: Node): boolean {
  return (
    Math.round(a.position?.x ?? 0) !== Math.round(b.position?.x ?? 0) ||
    Math.round(a.position?.y ?? 0) !== Math.round(b.position?.y ?? 0)
  )
}

/**
 * Diff two pipeline graphs by node: which components were added (in NEW only),
 * removed (in OLD only), changed (same id, different content), or merely moved
 * (same id and content, different position). Matching is by node id, which is
 * stable across commits while the underlying function name is; a renamed node
 * surfaces as one removed + one added. A changed node is not also reported as
 * moved — the content change is the headline.
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
  const moved = new Set<string>()

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
    else if (positionChanged(oldNode, newNode)) moved.add(id)
  }
  return { added, removed, changed, moved }
}
