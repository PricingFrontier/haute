import { useLayoutEffect, useRef } from "react"
import { useNodesInitialized, useReactFlow } from "@xyflow/react"

const INITIAL_FIT_OPTIONS = { padding: 0.15 }

/**
 * Fits the graph into view once per canvas mount, the first time every node has been measured.
 *
 * React Flow's `fitView` prop resolves on the first batch of node measurements and fits only the
 * nodes measured by then, which can zoom onto a single node of a larger graph. Rendered inside
 * `<ReactFlow>` so its store subscription re-renders only this component.
 */
export default function InitialViewFit() {
  const nodesInitialized = useNodesInitialized()
  const { fitView } = useReactFlow()
  const fittedRef = useRef(false)

  // A layout effect fits before the browser paints the unfitted viewport.
  useLayoutEffect(() => {
    if (fittedRef.current || !nodesInitialized) return
    fittedRef.current = true
    void fitView(INITIAL_FIT_OPTIONS)
  }, [nodesInitialized, fitView])

  return null
}
