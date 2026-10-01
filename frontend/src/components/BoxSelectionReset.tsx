import { useLayoutEffect, useRef } from "react"
import { useStore, useStoreApi, type ReactFlowState } from "@xyflow/react"

/** The selected node ids while a box-selection group is active, or null when none is. */
function groupSelectionKey(state: ReactFlowState): string | null {
  if (!state.nodesSelectionActive) return null
  return JSON.stringify(state.nodes.filter((node) => node.selected).map((node) => node.id).sort())
}

/**
 * Ends React Flow's box-selection group as soon as the selection is no longer the one the box made.
 *
 * React Flow holds a box selection as a group under a rectangle that takes the selected nodes'
 * pointer events (Haute draws it transparent), and ends the group only through its own gestures:
 * pane, node and edge clicks, Delete, the next box. A selection the editor makes itself — a palette
 * drop, paste, undo — would leave the rectangle over the new selection, swallowing its clicks and
 * context menus, and in React Flow 12.10 its drag too once the rectangle remounts around a node
 * that has not been measured yet.
 * Rendered inside `<ReactFlow>` so its store subscription re-renders only this component.
 */
export default function BoxSelectionReset() {
  const store = useStoreApi()
  const selectionKey = useStore(groupSelectionKey)
  const boxSelectionKeyRef = useRef<string | null>(null)

  // A layout effect ends the group before the browser paints its rectangle over the new selection.
  useLayoutEffect(() => {
    if (selectionKey === null) {
      boxSelectionKeyRef.current = null
      return
    }
    if (boxSelectionKeyRef.current === null) {
      boxSelectionKeyRef.current = selectionKey
      return
    }
    if (selectionKey !== boxSelectionKeyRef.current) store.setState({ nodesSelectionActive: false })
  }, [selectionKey, store])

  return null
}
