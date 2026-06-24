/**
 * Single source of truth for "can this selection be grouped into a wrapper?".
 *
 * Used by the action guards (right-click "Group into wrapper" + Ctrl+G) AND by
 * the selection context menu's disabled/greyed-out state, so the rule and its
 * user-facing reason can't drift between "the menu says you can't" and "the
 * action refuses". Returns the reason string when blocked (suitable for a toast
 * or a disabled-item tooltip), or null when grouping is allowed. User-facing
 * copy says "wrapper", not "submodel".
 */
import type { Node } from "@xyflow/react"
import { nodeData } from "../types/node"
import { NODE_TYPES } from "./nodeTypes"

export function groupIntoWrapperBlockedReason(params: {
  /** The current canvas nodes (to resolve selected node types). */
  nodes: Node[]
  /** Ids of the selected nodes the group action would apply to. */
  selectedIds: string[]
  /** True when viewing inside a wrapper (drilled in) — nesting is disallowed. */
  isInsideWrapper: boolean
}): string | null {
  const { nodes, selectedIds, isInsideWrapper } = params
  if (isInsideWrapper) return "Wrappers can't be nested inside other wrappers"
  if (selectedIds.length < 2) return "Select at least 2 nodes to create a wrapper"
  const ids = new Set(selectedIds)
  const includesWrapper = nodes.some(
    (n) => ids.has(n.id) && nodeData(n).nodeType === NODE_TYPES.SUBMODEL,
  )
  if (includesWrapper) return "A wrapper can't contain another wrapper — deselect it first"
  return null
}
