import { useEffect, useRef } from "react"
import { useReactFlow } from "@xyflow/react"
import useUIStore from "../stores/useUIStore"

const CHANGE_FIT_OPTIONS = { padding: 0.3, duration: 300 }

/**
 * Centres the nodes the latest assistant change or undo touched, once per focus:
 * it fits them with padding but never zooms in past the current zoom, so a
 * one-node change does not fill the screen. Rendered inside `<ReactFlow>`.
 */
export default function ChangeFocusFit() {
  const { fitView, getZoom } = useReactFlow()
  const changeFocus = useUIStore((s) => s.changeFocus)
  const centredRef = useRef<typeof changeFocus>(null)

  useEffect(() => {
    if (!changeFocus || centredRef.current === changeFocus) return
    centredRef.current = changeFocus
    void fitView({
      nodes: changeFocus.nodeIds.map((id) => ({ id })),
      maxZoom: getZoom(),
      ...CHANGE_FIT_OPTIONS,
    })
  }, [changeFocus, fitView, getZoom])

  return null
}
