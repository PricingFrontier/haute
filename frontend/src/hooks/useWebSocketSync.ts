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
// nodes are committed. This guard window lets that spurious event pass.
const SELECTION_CHANGE_GUARD_MS = 150
const INITIAL_BACKOFF_MS = 1_000
const MAX_BACKOFF_MS = 30_000

function formatSyncError(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}

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
    let graphUpdateSeq = 0
    let activeSelectionGuardIncrements = 0
    const delayedTimers = new Set<ReturnType<typeof setTimeout>>()

    function scheduleDelayed(callback: () => void, delayMs: number) {
      const timer = setTimeout(() => {
        delayedTimers.delete(timer)
        callback()
      }, delayMs)
      delayedTimers.add(timer)
    }

    function releaseSelectionGuard() {
      if (activeSelectionGuardIncrements > 0) {
        activeSelectionGuardIncrements -= 1
      }
      graphRefreshingRef.current = Math.max(0, graphRefreshingRef.current - 1)
    }

    function connect() {
      if (!mounted) return
      try {
        ws = new WebSocket(wsUrl)
      } catch (err) {
        if (!mounted) return
        setStatus("disconnected")
        addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
        return
      }

      ws.onopen = () => {
        if (!mounted) return
        retriesRef.current = 0
        setStatus("connected")
      }

      ws.onmessage = async (event) => {
        let msg: Record<string, unknown>
        try {
          msg = JSON.parse(event.data)
        } catch (err) {
          addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
          return
        }

        if (msg.type === "graph_update" && msg.graph) {
          const updateSeq = ++graphUpdateSeq
          const g = msg.graph as {
            nodes?: Node[]
            edges?: Edge[]
            preamble?: string
            warning?: string
          }

          try {
            const newNodes = g.nodes || []
            const newEdges = normalizeEdges(g.edges || [])
            const hasPositions = newNodes.some(
              (n: Node) => n.position && (n.position.x !== 0 || n.position.y !== 0),
            )
            const nodesToApply = hasPositions
              ? newNodes
              : await getLayoutedElements(newNodes, newEdges)

            if (!mounted || updateSeq !== graphUpdateSeq) {
              return
            }

            const previousGraph = useGraphStore.getState() as Partial<{
              nodes: Node[]
              edges: Edge[]
              preamble: string
            }>
            const canRollback =
              Array.isArray(previousGraph.nodes) && Array.isArray(previousGraph.edges)
            const previousPreamble =
              typeof previousGraph.preamble === "string"
                ? previousGraph.preamble
                : preambleRef.current

            // Guard: prevent React Flow's onSelectionChange from clearing
            // the open panel while we replace nodes.
            graphRefreshingRef.current += 1
            activeSelectionGuardIncrements += 1
            try {
              setNodesRaw(nodesToApply)
              setEdgesRaw(newEdges)
              const nextPreamble = g.preamble !== undefined
                ? (g.preamble || "")
                : preambleRef.current
              if (g.preamble !== undefined) {
                setPreamble(nextPreamble)
                preambleRef.current = nextPreamble
              }
              nodeIdCounter.current = computeNextNodeId(newNodes)
              setSyncBanner(null)
              useGraphStore.getState().markSaved()
            } catch (err) {
              if (canRollback) {
                try {
                  setNodesRaw(previousGraph.nodes!)
                  setEdgesRaw(previousGraph.edges!)
                  if (g.preamble !== undefined) {
                    setPreamble(previousPreamble)
                    preambleRef.current = previousPreamble
                  }
                } catch {
                  // Keep the original sync error; the toast below still
                  // tells the user the refresh did not apply cleanly.
                }
              }
              throw err
            } finally {
              scheduleDelayed(releaseSelectionGuard, SELECTION_CHANGE_GUARD_MS)
            }

            // Clear UI that references nodes removed by this graph update.
            const newNodeIds = new Set<string>(newNodes.map((n) => n.id))
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
            scheduleDelayed(() => {
              if (mounted && updateSeq === graphUpdateSeq) {
                fitView({ padding: 0.8 })
              }
            }, 100)
          } catch (err) {
            if (!mounted || updateSeq !== graphUpdateSeq) {
              return
            }
            addToast("error", `WebSocket sync error: ${formatSyncError(err)}`)
          }
          return
        }

        if (msg.type === "parse_error") {
          setSyncBanner(String(msg.error || "Parse error in pipeline file"))
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
      for (const timer of delayedTimers) {
        clearTimeout(timer)
      }
      delayedTimers.clear()
      if (activeSelectionGuardIncrements > 0) {
        graphRefreshingRef.current = Math.max(
          0,
          graphRefreshingRef.current - activeSelectionGuardIncrements,
        )
        activeSelectionGuardIncrements = 0
      }
      ws?.close()
    }
  }, [setNodesRaw, setEdgesRaw, setPreamble, preambleRef, nodeIdCounter, fitView, setSyncBanner, addToast, graphRefreshingRef])

  return status
}
