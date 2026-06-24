/**
 * Wires the pure pan/right-click state machine (`panController`) to the DOM and
 * the React Flow viewport.
 *
 * Attaches a pointer-down + contextmenu listener to the canvas wrapper, runs
 * the menu-debounce timer, drives the viewport via the imperative
 * `setViewport` API, and hit-tests the press target to decide which context
 * menu (if any) to open. See `panController` for the gesture rules.
 *
 * The wrapper owns ALL canvas context menus: the native browser menu is always
 * suppressed over canvas content, and menus open only through the gesture (so a
 * right-drag-to-pan never flashes a menu). React Flow's own `onNodeContextMenu`
 * / `onSelectionContextMenu` / `panOnDrag` are left disconnected.
 */
import { useCallback, useEffect, useRef } from "react"
import { useReactFlow } from "@xyflow/react"
import {
  reducePan,
  IDLE,
  DEFAULT_PAN_CONFIG,
  MIDDLE_BUTTON,
  RIGHT_BUTTON,
  type PanState,
  type PanConfig,
  type PanCommand,
  type PanEvent,
} from "./panController"

/** What the press landed on, resolved from the canvas DOM. */
export interface CanvasContextHit {
  /** The React Flow node id under the press, or null for an edge / the pane. */
  nodeId: string | null
  /**
   * The press landed on the multi-selection drag overlay
   * (`.react-flow__nodesselection-rect`, which sits above the selected nodes
   * with `pointer-events: all`). It targets the whole selection, not one node —
   * without this a right-click on a multi-selection resolves to a null node id
   * (the overlay isn't a `.react-flow__node`) and no menu opens.
   */
  onSelection?: boolean
}

export interface UseCanvasPanOptions {
  /** Open the appropriate context menu for a resolved press, or nothing. */
  onContextMenu: (hit: CanvasContextHit, clientX: number, clientY: number) => void
  config?: PanConfig
}

/** A callback ref to spread onto the canvas wrapper element. */
export type CanvasWrapperRef = (el: HTMLElement | null) => void

/** Canvas content the gesture acts on. Overlays (peek, breadcrumb) are excluded.
 *  Includes the multi-selection drag rect so right-drag still pans over it and a
 *  right-click there opens the selection menu (not the suppressed native one). */
const CANVAS_SELECTOR =
  ".react-flow__pane, .react-flow__node, .react-flow__edge, .react-flow__edges, .react-flow__nodesselection"

export default function useCanvasPan({
  onContextMenu,
  config = DEFAULT_PAN_CONFIG,
}: UseCanvasPanOptions): CanvasWrapperRef {
  const { getViewport, setViewport } = useReactFlow()
  // Keep the latest callback in a ref so the gesture listeners (attached once
  // per wrapper) always call through to it without re-subscribing every render.
  const onContextMenuRef = useRef(onContextMenu)
  useEffect(() => {
    onContextMenuRef.current = onContextMenu
  }, [onContextMenu])

  // An imperative callback ref (not a RefObject + effect): it attaches the
  // listeners the moment the wrapper mounts — even behind a loading gate, where
  // a plain ref object would leave the effect pinned to a null element forever
  // — and crucially does NOT go through component state, so the canvas mounting
  // never triggers a React re-render (the toolbar's render isolation depends on
  // FlowEditor not re-rendering on canvas mount).
  const cleanupRef = useRef<(() => void) | null>(null)
  const wrapperRef = useCallback(
    (wrapper: HTMLElement | null) => {
      cleanupRef.current?.()
      cleanupRef.current = null
      if (!wrapper) return

      let state: PanState = IDLE
      let timer: ReturnType<typeof setTimeout> | null = null
      let downTarget: Element | null = null
      let windowAttached = false

      const clearTimer = () => {
        if (timer !== null) {
          clearTimeout(timer)
          timer = null
        }
      }

      const resolveHit = (): CanvasContextHit => {
        // A press on the multi-selection drag overlay targets the whole
        // selection — resolve it before the per-node lookup (the overlay is not
        // a `.react-flow__node`, so that lookup would otherwise return null).
        if (downTarget?.closest(".react-flow__nodesselection")) {
          return { nodeId: null, onSelection: true }
        }
        const nodeEl = downTarget?.closest(".react-flow__node")
        return { nodeId: nodeEl?.getAttribute("data-id") ?? null }
      }

      // Capture phase throughout: React Flow stops propagation of pointer events
      // on nodes/edges, so a bubble-phase listener on the wrapper would never see
      // a press that started on a node. Capturing means we always see the gesture
      // first — and we deliberately never stopPropagation, so React Flow still
      // gets left-button events for node-drag and marquee select.
      const attachWindow = () => {
        if (windowAttached) return
        window.addEventListener("pointermove", onWindowMove, true)
        window.addEventListener("pointerup", onWindowUp, true)
        window.addEventListener("pointercancel", onWindowCancel, true)
        windowAttached = true
      }
      const detachWindow = () => {
        if (!windowAttached) return
        window.removeEventListener("pointermove", onWindowMove, true)
        window.removeEventListener("pointerup", onWindowUp, true)
        window.removeEventListener("pointercancel", onWindowCancel, true)
        windowAttached = false
      }

      const apply = (commands: PanCommand[]) => {
        for (const cmd of commands) {
          switch (cmd.type) {
            case "startMenuTimer":
              clearTimer()
              timer = setTimeout(() => dispatch({ type: "menuTimer" }), config.menuDebounceMs)
              break
            case "cancelMenuTimer":
              clearTimer()
              break
            case "panBy": {
              const vp = getViewport()
              setViewport({ x: vp.x + cmd.dx, y: vp.y + cmd.dy, zoom: vp.zoom })
              break
            }
            case "openMenu":
              onContextMenuRef.current(resolveHit(), cmd.x, cmd.y)
              break
            case "beginPan":
            case "endPan":
              // Movement is handled by panBy; nothing imperative to do here.
              break
          }
        }
      }

      const dispatch = (event: PanEvent) => {
        const next = reducePan(state, event, config)
        state = next.state
        apply(next.commands)
        if (state.kind === "idle") {
          clearTimer()
          detachWindow()
        }
      }

      function onWindowMove(e: PointerEvent) {
        dispatch({ type: "pointerMove", x: e.clientX, y: e.clientY })
      }
      function onWindowUp(e: PointerEvent) {
        dispatch({ type: "pointerUp", button: e.button })
      }
      function onWindowCancel() {
        dispatch({ type: "cancel" })
      }

      const onPointerDown = (e: PointerEvent) => {
        if (e.button !== MIDDLE_BUTTON && e.button !== RIGHT_BUTTON) return
        const target = e.target as Element | null
        // No `.nopan` exclusion: React Flow tags every node wrapper `.nopan` to
        // stop ITS pane-pan starting on a node, but we disabled that pan and want
        // the gesture to work from nodes (middle pan / right menu) by design.
        const overCanvas = !!target?.closest(CANVAS_SELECTOR)
        if (!overCanvas || !target) return
        // Stop the middle-click autoscroll puck before it appears.
        if (e.button === MIDDLE_BUTTON) e.preventDefault()
        downTarget = target
        attachWindow()
        dispatch({ type: "pointerDown", button: e.button, x: e.clientX, y: e.clientY, overCanvas })
      }

      // We own canvas context menus; never let the browser's native menu through
      // on pane / node / edge (an edge press would otherwise fall through to it).
      const onContextMenuEvent = (e: MouseEvent) => {
        const target = e.target as Element | null
        if (target?.closest(CANVAS_SELECTOR)) e.preventDefault()
      }

      wrapper.addEventListener("pointerdown", onPointerDown, true)
      wrapper.addEventListener("contextmenu", onContextMenuEvent, true)
      cleanupRef.current = () => {
        wrapper.removeEventListener("pointerdown", onPointerDown, true)
        wrapper.removeEventListener("contextmenu", onContextMenuEvent, true)
        detachWindow()
        clearTimer()
      }
    },
    [getViewport, setViewport, config],
  )

  return wrapperRef
}
