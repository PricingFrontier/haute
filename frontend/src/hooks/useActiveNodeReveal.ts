/**
 * Keeps the inspector's active node visible in the canvas area its own UI leaves.
 *
 * The inspector panel and preview pane take their space from the canvas, and React Flow keeps
 * the viewport anchored at the top-left, so opening them can clip the node they describe. Each
 * time a node becomes active the hook places it inside the remaining canvas, then keeps doing so
 * as that area changes, until the user pans or zooms. See the graph-canvas spec's
 * "Active node visibility".
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { useReactFlow, useStoreApi, type OnMoveStart, type Viewport } from "@xyflow/react"

import { nodeRevealViewport, type NodeRevealPlacement } from "../utils/nodeReveal"

/** The first placement's glide, matching the inspector's 0.2s slide-in. */
export const NODE_REVEAL_DURATION_MS = 200

const NEAREST: NodeRevealPlacement = { kind: "nearest" }

type ArmedReveal = {
  nodeId: string
  placement: NodeRevealPlacement
  firstPlacementOwed: boolean
}

type CentreRequest = { nodeId: string; zoom: number }

export function useActiveNodeReveal(activeNodeId: string | null): {
  handleMoveStart: OnMoveStart
  centreNode: (nodeId: string, zoom: number) => void
} {
  const store = useStoreApi()
  const { setViewport } = useReactFlow()
  const armedRef = useRef<ArmedReveal | null>(null)
  const lastActiveIdRef = useRef<string | null>(null)
  const glideRef = useRef<{ target: Viewport; endsAt: number } | null>(null)
  const measuredKeyRef = useRef<string | null>(null)
  const attemptScheduledRef = useRef(false)
  const consumedRequestRef = useRef<CentreRequest | null>(null)
  const [centreRequest, setCentreRequest] = useState<CentreRequest | null>(null)

  const attemptPlacement = useCallback(() => {
    const armed = armedRef.current
    if (!armed) return
    const { domNode, nodeLookup, transform } = store.getState()
    const node = nodeLookup.get(armed.nodeId)
    const width = node?.measured.width
    const height = node?.measured.height
    // A node React Flow has not measured yet retries when its measurement lands.
    if (!domNode?.isConnected || !node || !width || !height) return

    const now = performance.now()
    const glide = glideRef.current && glideRef.current.endsAt > now ? glideRef.current : null
    const [x, y, zoom] = transform
    const base = glide?.target ?? { x, y, zoom }
    const target = nodeRevealViewport(
      base,
      { ...node.internals.positionAbsolute, width, height },
      { width: domNode.clientWidth, height: domNode.clientHeight },
      armed.placement,
    )
    const animate = armed.firstPlacementOwed || glide !== null
    armed.firstPlacementOwed = false
    armed.placement = NEAREST
    if (!target) return
    if (animate) {
      glideRef.current = { target, endsAt: now + NODE_REVEAL_DURATION_MS }
      // d3's default smooth interpolation zooms out mid-flight; a zoom-preserving move stays a pan.
      const interpolate = target.zoom === base.zoom ? "linear" : "smooth"
      void setViewport(target, { duration: NODE_REVEAL_DURATION_MS, interpolate })
    } else {
      glideRef.current = null
      void setViewport(target)
    }
  }, [store, setViewport])

  useLayoutEffect(() => {
    const pending = centreRequest !== consumedRequestRef.current ? centreRequest : null
    consumedRequestRef.current = centreRequest
    const centre = pending?.nodeId === activeNodeId ? pending : null
    if (activeNodeId === lastActiveIdRef.current && !centre) return
    lastActiveIdRef.current = activeNodeId
    if (!activeNodeId) {
      armedRef.current = null
      return
    }
    armedRef.current = {
      nodeId: activeNodeId,
      placement: centre ? { kind: "centre", zoom: centre.zoom } : NEAREST,
      firstPlacementOwed: true,
    }
    measuredKeyRef.current = measuredKey(store.getState().nodeLookup.get(activeNodeId))
    attemptPlacement()
  }, [activeNodeId, centreRequest, store, attemptPlacement])

  useEffect(() => store.subscribe((state, previous) => {
    const armed = armedRef.current
    if (!armed) return
    const key = measuredKey(state.nodeLookup.get(armed.nodeId))
    const measurementChanged = key !== measuredKeyRef.current
    measuredKeyRef.current = key
    if (!measurementChanged && state.width === previous.width && state.height === previous.height) return
    // Never move the viewport from inside a store listener; React Flow may be mid-update.
    if (attemptScheduledRef.current) return
    attemptScheduledRef.current = true
    queueMicrotask(() => {
      attemptScheduledRef.current = false
      attemptPlacement()
    })
  }), [store, attemptPlacement])

  const handleMoveStart = useCallback<OnMoveStart>((event) => {
    // Only a DOM event is a user gesture; programmatic moves (glides, fitView, auto-pan) carry none.
    if (!(event instanceof Event)) return
    armedRef.current = null
    glideRef.current = null
  }, [])

  const centreNode = useCallback((nodeId: string, zoom: number) => {
    setCentreRequest({ nodeId, zoom })
  }, [])

  return { handleMoveStart, centreNode }
}

function measuredKey(node: { measured: { width?: number; height?: number } } | undefined): string | null {
  return node ? `${node.measured.width}x${node.measured.height}` : null
}
