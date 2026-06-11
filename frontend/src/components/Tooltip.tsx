import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react"
import type { ReactNode } from "react"
import { createPortal } from "react-dom"

/**
 * Generic, dependency-free hover tooltip (tooltips-descriptions design §3.2).
 *
 * Render-prop API: the child function receives trigger props to spread onto
 * an EXISTING element — no wrapper DOM, so layouts and React Flow node
 * measurements are untouched. The popover renders through a portal to
 * document.body with `pointer-events: none` (read-only; it can never
 * intercept canvas clicks or flicker under the cursor).
 *
 * Open: sustained hover for `delayMs`, or keyboard focus (no delay).
 * Close: trigger mouseleave/blur, Escape, any window pointerdown (drag
 * start), any scroll/wheel (capture — palette scroll, canvas pan/zoom),
 * trigger unmount. While a pointer button is held anywhere (node drag,
 * connection drag, rubber-band select) the open timer never starts and a
 * running one is cancelled — a tooltip must never pop open mid-drag.
 * The `disabled` prop suppresses/dismisses the same way and lets callers
 * feed in gesture state the pointer heuristic can't see (e.g. React Flow's
 * `connection.inProgress` during click-to-connect).
 *
 * Reusable beyond node-type tooltips (orphan-edge tooltip, instance
 * descriptions) — pass a custom `testId` per surface where needed.
 */

export type TooltipPlacement = "top" | "bottom" | "left" | "right"

export interface TooltipTriggerProps {
  ref: (el: HTMLElement | null) => void
  onMouseEnter: () => void
  onMouseLeave: () => void
  onFocus: () => void
  onBlur: () => void
  "aria-describedby": string | undefined
}

/** Gap between trigger and popover, px. */
const TRIGGER_GAP_PX = 8
/** Minimum clearance from the viewport edges, px. */
const VIEWPORT_MARGIN_PX = 8

const OPPOSITE: Record<TooltipPlacement, TooltipPlacement> = {
  top: "bottom",
  bottom: "top",
  left: "right",
  right: "left",
}

function _clamp(value: number, min: number, max: number): number {
  return Math.min(Math.max(value, min), Math.max(min, max))
}

/** Fixed-position coordinates for the popover; flips to the opposite side on overflow. */
function _computePosition(
  placement: TooltipPlacement,
  trigger: DOMRect,
  popover: DOMRect,
  viewportWidth: number,
  viewportHeight: number,
): { left: number; top: number } {
  const fits: Record<TooltipPlacement, boolean> = {
    top: trigger.top - TRIGGER_GAP_PX - popover.height >= VIEWPORT_MARGIN_PX,
    bottom: trigger.bottom + TRIGGER_GAP_PX + popover.height <= viewportHeight - VIEWPORT_MARGIN_PX,
    left: trigger.left - TRIGGER_GAP_PX - popover.width >= VIEWPORT_MARGIN_PX,
    right: trigger.right + TRIGGER_GAP_PX + popover.width <= viewportWidth - VIEWPORT_MARGIN_PX,
  }
  // Flip only when the requested side overflows AND the opposite side fits.
  const side = !fits[placement] && fits[OPPOSITE[placement]] ? OPPOSITE[placement] : placement

  if (side === "top" || side === "bottom") {
    const left = _clamp(
      trigger.left + trigger.width / 2 - popover.width / 2,
      VIEWPORT_MARGIN_PX,
      viewportWidth - VIEWPORT_MARGIN_PX - popover.width,
    )
    const top = side === "top"
      ? trigger.top - TRIGGER_GAP_PX - popover.height
      : trigger.bottom + TRIGGER_GAP_PX
    return { left, top }
  }
  const top = _clamp(
    trigger.top + trigger.height / 2 - popover.height / 2,
    VIEWPORT_MARGIN_PX,
    viewportHeight - VIEWPORT_MARGIN_PX - popover.height,
  )
  const left = side === "left"
    ? trigger.left - TRIGGER_GAP_PX - popover.width
    : trigger.right + TRIGGER_GAP_PX
  return { left, top }
}

export default function Tooltip({
  content,
  children,
  placement = "top",
  delayMs = 300,
  disabled = false,
  testId = "node-type-tooltip",
}: {
  content: ReactNode
  children: (triggerProps: TooltipTriggerProps) => ReactNode
  placement?: TooltipPlacement
  delayMs?: number
  /** Gesture suppression: while true, never open; close immediately if open. */
  disabled?: boolean
  testId?: string
}) {
  const [open, setOpen] = useState(false)
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null)
  const triggerRef = useRef<HTMLElement | null>(null)
  const popoverRef = useRef<HTMLDivElement | null>(null)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const pointerHeldRef = useRef(false)
  const popoverId = useId()

  const cancelTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const close = useCallback(() => {
    cancelTimer()
    setOpen(false)
  }, [cancelTimer])

  // Track whether a pointer button is held anywhere. Every canvas gesture
  // (node drag, connection drag, rubber-band select) and every palette drag
  // begins with pointerdown — so pointerdown both dismisses an open tooltip
  // and blocks opening until the button is released. pointercancel/dragend
  // cover HTML5 drags where pointerup never fires.
  useEffect(() => {
    const onPointerDown = () => {
      pointerHeldRef.current = true
      close()
    }
    const onPointerRelease = () => {
      pointerHeldRef.current = false
    }
    window.addEventListener("pointerdown", onPointerDown, true)
    window.addEventListener("pointerup", onPointerRelease, true)
    window.addEventListener("pointercancel", onPointerRelease, true)
    window.addEventListener("dragend", onPointerRelease, true)
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true)
      window.removeEventListener("pointerup", onPointerRelease, true)
      window.removeEventListener("pointercancel", onPointerRelease, true)
      window.removeEventListener("dragend", onPointerRelease, true)
    }
  }, [close])

  // Close-on-gesture listeners, attached only while open: scroll/wheel in
  // the capture phase (palette scroll, canvas pan/zoom — also moots stale
  // anchors under zoom) and Escape.
  useEffect(() => {
    if (!open) return
    const onScrollOrWheel = () => close()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") close()
    }
    window.addEventListener("scroll", onScrollOrWheel, true)
    window.addEventListener("wheel", onScrollOrWheel, true)
    window.addEventListener("keydown", onKeyDown, true)
    return () => {
      window.removeEventListener("scroll", onScrollOrWheel, true)
      window.removeEventListener("wheel", onScrollOrWheel, true)
      window.removeEventListener("keydown", onKeyDown, true)
    }
  }, [open, close])

  // A gesture started mid-open (disabled flipped true) → dismiss instantly.
  // Render-time state adjustment (the React-docs alternative to an effect;
  // the compiler lint forbids synchronous setState inside effects). Closing
  // on EITHER flip direction also prevents a stale tooltip resurrecting when
  // disabled returns to false. The pending-open timer needs no cancel here:
  // every gesture that flips disabled begins with pointerdown, whose capture
  // listener already closed and cancelled; the timer callback re-checks
  // pointerHeldRef as a final guard.
  const [prevDisabled, setPrevDisabled] = useState(disabled)
  if (disabled !== prevDisabled) {
    setPrevDisabled(disabled)
    if (open) setOpen(false)
  }

  // Unmount cleanup: cancel any pending open (the portal unmounts with us).
  useEffect(() => cancelTimer, [cancelTimer])

  // Position after first paint of the popover; flips/clamps to the viewport.
  // No null-reset on close: the portal only renders while open, and on the
  // next open this layout effect re-measures and re-renders BEFORE the
  // browser paints, so a stale position is never visible.
  useLayoutEffect(() => {
    if (!open) return
    const trigger = triggerRef.current
    const popover = popoverRef.current
    if (!trigger || !popover) return
    setPosition(
      _computePosition(
        placement,
        trigger.getBoundingClientRect(),
        popover.getBoundingClientRect(),
        window.innerWidth,
        window.innerHeight,
      ),
    )
  }, [open, placement])

  // Memoised callback ref: writes happen at React's commit phase, never
  // during render (compiler-lint react-hooks/refs requires the indirection).
  const setTriggerEl = useCallback((el: HTMLElement | null) => {
    triggerRef.current = el
  }, [])

  const startOpenTimer = useCallback(() => {
    if (disabled || pointerHeldRef.current) return
    cancelTimer()
    timerRef.current = setTimeout(() => {
      timerRef.current = null
      if (pointerHeldRef.current) return
      setOpen(true)
    }, delayMs)
  }, [cancelTimer, delayMs, disabled])

  const openImmediately = useCallback(() => {
    if (disabled || pointerHeldRef.current) return
    cancelTimer()
    setOpen(true)
  }, [cancelTimer, disabled])

  const triggerProps: TooltipTriggerProps = {
    ref: setTriggerEl,
    onMouseEnter: startOpenTimer,
    onMouseLeave: close,
    onFocus: openImmediately,
    onBlur: close,
    "aria-describedby": open ? popoverId : undefined,
  }

  return (
    <>
      {/* eslint-disable-next-line react-hooks/refs -- false positive on the
          render-prop indirection: children only spreads these props onto JSX
          (verified at every call site); the ref callback runs at React's
          commit phase, never during render. */}
      {children(triggerProps)}
      {open &&
        createPortal(
          <div
            ref={popoverRef}
            id={popoverId}
            role="tooltip"
            data-testid={testId}
            className="rounded-lg px-3 py-2 max-w-[280px] animate-fade-in"
            style={{
              position: "fixed",
              left: position?.left ?? 0,
              top: position?.top ?? 0,
              visibility: position ? "visible" : "hidden",
              pointerEvents: "none",
              background: "var(--bg-elevated)",
              border: "1px solid var(--border-bright)",
              boxShadow: "var(--node-shadow)",
              zIndex: 200,
            }}
          >
            {content}
          </div>,
          document.body,
        )}
    </>
  )
}
