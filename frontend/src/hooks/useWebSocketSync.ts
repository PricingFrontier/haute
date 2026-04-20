import { useEffect, useRef, useState } from "react"
import type { Node, Edge } from "@xyflow/react"
import { getLayoutedElements } from "../utils/layout"
import { computeNextNodeId, normalizeEdges } from "../utils/graphHelpers"
import useToastStore from "../stores/useToastStore"
import useUIStore from "../stores/useUIStore"
import useGraphStore from "../stores/useGraphStore"

export type WsStatus = "connected" | "reconnecting" | "disconnected"

interface WebSocketSyncParams {
  setNodesRaw: (nodes: Node[]) => void
  setEdgesRaw: (edges: Edge[]) => void
  setPreamble: (p: string) => void
  preambleRef: React.MutableRefObject<string>
  graphRefreshingRef: React.MutableRefObject<number>
  nodeIdCounter: React.MutableRefObject<number>
  fitView: (options?: { padding?: number }) => void
}

const MAX_RETRIES = 50

// After replacing nodes, React Flow fires onSelectionChange before the new
// nodes are committed.  This guard window lets that spurious event pass.
const SELECTION_CHANGE_GUARD_MS = 150
const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000

export default function useWebSocketSync({
  setNodesRaw, setEdgesRaw, setPreamble, preambleRef, graphRefreshingRef,
  nodeIdCounter, fitView,
}: WebSocketSyncParams): WsStatus {
  const { setSyncBanner } = useUIStore()
  const { addToast } = useToastStore()
  const [status, setStatus] = useState<WsStatus>("reconnecting")
  const retriesRef = useRef(0)

  useEffect(() => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:"
    const wsUrl = `${protocol}//${window.location.host}/ws/sync`
    let ws: WebSocket | null = null
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null
    let mounted = true

    function connect() {
      if (!mounted) return
      ws = new WebSocket(wsUrl)

      ws.onopen = () => {
        if (!mounted) return
        retriesRef.current = 0
        setStatus("connected")
      }

      ws.onmessage = async (event) => {
        try {
          const msg = JSON.parse(event.data)

          if (msg.type === "graph_update" && msg.graph) {

            const g = msg.graph
            const newNodes = g.nodes || []
            const newEdges = normalizeEdges(g.edges || [])

            const hasPositions = newNodes.some(
              (n: Node) => n.position && (n.position.x !== 0 || n.position.y !== 0)
            )

            // Guard: prevent React Flow's onSelectionChange from clearing
            // the open panel while we replace nodes.
            graphRefreshingRef.current += 1
            try {
              // Capture the exact nodes+edges we hand to React Flow so
              // the saved snapshot matches what lives in state. If we
              // compute the layout, the *layouted* nodes (which carry
              // assigned positions) are what the GUI will render — the
              // raw nodes without positions would diverge immediately.
              if (hasPositions) {
                setNodesRaw(newNodes)
              } else {
                const layouted = await getLayoutedElements(newNodes, newEdges)
                setNodesRaw(layouted)
              }
              setEdgesRaw(newEdges)
              const nextPreamble = g.preamble !== undefined ? (g.preamble || "") : preambleRef.current
              if (g.preamble !== undefined) {
                setPreamble(nextPreamble)
                preambleRef.current = nextPreamble
              }
              nodeIdCounter.current = computeNextNodeId(newNodes)
              setSyncBanner(null)
              // The GUI is now in sync with the file on disk — mark the
              // current store state as saved so isDirty returns false.
              // The preceding setNodesRaw / setEdgesRaw / setPreamble have
              // already written into useGraphStore, so the capture here
              // matches what we just loaded.
              useGraphStore.getState().markSaved()

              // Issue #39: clear any open dialog whose target node was
              // removed by this graph update.  Leaving an orphaned
              // renameDialog / submodelDialog means onConfirm would fire
              // with a nodeId that no longer exists in the graph.
              const newNodeIds = new Set<string>(
                (newNodes as Array<{ id: string }>).map((n) => n.id),
              )
              const ui = useUIStore.getState()
              if (ui.renameDialog && !newNodeIds.has(ui.renameDialog.nodeId)) {
                ui.setRenameDialog(null)
              }
              if (
                ui.submodelDialog &&
                ui.submodelDialog.nodeIds.some((id) => !newNodeIds.has(id))
              ) {
                ui.setSubmodelDialog(null)
              }

              addToast("info", "Pipeline updated from file")
              if (g.warning) addToast("warning", g.warning)
              setTimeout(() => fitView({ padding: 0.8 }), 100)
            } finally {
              setTimeout(() => { graphRefreshingRef.current -= 1 }, SELECTION_CHANGE_GUARD_MS)
            }
          }

          if (msg.type === "parse_error") {
            setSyncBanner(msg.error || "Parse error in pipeline file")
          }
        } catch (err) {
          addToast("error", `WebSocket sync error: ${err instanceof Error ? err.message : String(err)}`)
        }
      }

      ws.onclose = () => {
        if (!mounted) return
        retriesRef.current += 1

        if (retriesRef.current > MAX_RETRIES) {
          setStatus("disconnected")
          return
        }

        setStatus("reconnecting")
        const backoff = Math.min(INITIAL_BACKOFF_MS * 2 ** (retriesRef.current - 1), MAX_BACKOFF_MS)
        reconnectTimer = setTimeout(connect, backoff)
      }

      ws.onerror = () => {
        ws?.close()
      }
    }

    connect()

    return () => {
      mounted = false
      if (reconnectTimer) clearTimeout(reconnectTimer)
      ws?.close()
    }
  }, [setNodesRaw, setEdgesRaw, setPreamble, preambleRef, nodeIdCounter, fitView, setSyncBanner, addToast, graphRefreshingRef])

  return status
}
