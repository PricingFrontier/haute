/**
 * tooltips-descriptions §5.2-A — the generic hover-tooltip primitive.
 *
 * Behaviour contract (design §3.2): render-prop trigger (no wrapper DOM),
 * portal popover with open delay, close on every canvas-relevant gesture
 * (mouseleave / Escape / pointerdown / scroll / wheel), no-delay open on
 * keyboard focus, viewport flip, and hard suppression while a pointer
 * button is held or the `disabled` prop is set — a tooltip must never pop
 * open mid-drag or fight a canvas gesture.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, act } from "@testing-library/react"

import Tooltip, { type TooltipTriggerProps } from "../Tooltip"

const DELAY = 300

function renderTooltip(overrides: Partial<React.ComponentProps<typeof Tooltip>> = {}) {
  return render(
    <Tooltip content={<span>tip body</span>} delayMs={DELAY} {...overrides}>
      {(triggerProps: TooltipTriggerProps) => (
        <button {...triggerProps} data-testid="tooltip-trigger" type="button">
          hover me
        </button>
      )}
    </Tooltip>,
  )
}

function openTooltip() {
  fireEvent.mouseEnter(screen.getByTestId("tooltip-trigger"))
  act(() => {
    vi.advanceTimersByTime(DELAY)
  })
  return screen.getByTestId("node-type-tooltip")
}

describe("Tooltip primitive", () => {
  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    cleanup()
    vi.useRealTimers()
  })

  it("does not render before delayMs and appears after it, portalled to document.body", () => {
    renderTooltip()
    fireEvent.mouseEnter(screen.getByTestId("tooltip-trigger"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(DELAY - 1)
    })
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
    act(() => {
      vi.advanceTimersByTime(1)
    })
    const popover = screen.getByTestId("node-type-tooltip")
    expect(popover).toHaveTextContent("tip body")
    // Portal assertion: the popover hangs off document.body, not the trigger's tree.
    expect(popover.parentElement).toBe(document.body)
  })

  it("never opens when the pointer leaves the trigger before the delay fires", () => {
    renderTooltip()
    const trigger = screen.getByTestId("tooltip-trigger")
    fireEvent.mouseEnter(trigger)
    act(() => {
      vi.advanceTimersByTime(100)
    })
    fireEvent.mouseLeave(trigger)
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("closes on trigger mouseleave", () => {
    renderTooltip()
    openTooltip()
    fireEvent.mouseLeave(screen.getByTestId("tooltip-trigger"))
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("closes on Escape", () => {
    renderTooltip()
    openTooltip()
    fireEvent.keyDown(window, { key: "Escape" })
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("closes on any window pointerdown (drag-start dismissal)", () => {
    renderTooltip()
    openTooltip()
    fireEvent.pointerDown(window)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("closes on window scroll (capture phase — palette scroll, canvas pan)", () => {
    renderTooltip()
    openTooltip()
    fireEvent.scroll(window)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("closes on wheel (canvas zoom)", () => {
    renderTooltip()
    openTooltip()
    fireEvent.wheel(window)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("opens on keyboard focus without delay and closes on blur", () => {
    renderTooltip()
    const trigger = screen.getByTestId("tooltip-trigger")
    fireEvent.focus(trigger)
    expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
    fireEvent.blur(trigger)
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("popover has role=tooltip and the trigger gets aria-describedby only while open", () => {
    renderTooltip()
    const trigger = screen.getByTestId("tooltip-trigger")
    expect(trigger).not.toHaveAttribute("aria-describedby")
    const popover = openTooltip()
    expect(popover).toHaveAttribute("role", "tooltip")
    expect(trigger).toHaveAttribute("aria-describedby", popover.id)
    fireEvent.mouseLeave(trigger)
    expect(trigger).not.toHaveAttribute("aria-describedby")
  })

  it("flips placement when the requested side would overflow the viewport", () => {
    renderTooltip({ placement: "right" })
    const trigger = screen.getByTestId("tooltip-trigger")
    // Anchor the trigger flush against the right viewport edge (jsdom
    // innerWidth = 1024): "right" placement cannot fit, must flip to "left".
    trigger.getBoundingClientRect = () =>
      ({ x: 1000, y: 100, left: 1000, right: 1024, top: 100, bottom: 120, width: 24, height: 20, toJSON: () => ({}) }) as DOMRect
    const popover = openTooltip()
    expect(parseFloat(popover.style.left)).toBeLessThan(1000)
  })

  it("positions on the requested side when it fits", () => {
    renderTooltip({ placement: "right" })
    const trigger = screen.getByTestId("tooltip-trigger")
    trigger.getBoundingClientRect = () =>
      ({ x: 100, y: 100, left: 100, right: 124, top: 100, bottom: 120, width: 24, height: 20, toJSON: () => ({}) }) as DOMRect
    const popover = openTooltip()
    // 8 px gap to the right of the trigger.
    expect(parseFloat(popover.style.left)).toBe(132)
  })

  it("removes the popover when the trigger unmounts while open (leak guard)", () => {
    const { unmount } = renderTooltip()
    openTooltip()
    unmount()
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("does not open while disabled", () => {
    renderTooltip({ disabled: true })
    fireEvent.mouseEnter(screen.getByTestId("tooltip-trigger"))
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("closes immediately when disabled becomes true while open (gesture started)", () => {
    const { rerender } = renderTooltip()
    openTooltip()
    rerender(
      <Tooltip content={<span>tip body</span>} delayMs={DELAY} disabled>
        {(triggerProps: TooltipTriggerProps) => (
          <button {...triggerProps} data-testid="tooltip-trigger" type="button">
            hover me
          </button>
        )}
      </Tooltip>,
    )
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
  })

  it("suppresses opening while a pointer button is held (mid-drag hover) and recovers after pointerup", () => {
    renderTooltip()
    const trigger = screen.getByTestId("tooltip-trigger")
    // Button goes down somewhere (drag start), pointer then transits the trigger.
    fireEvent.pointerDown(window)
    fireEvent.mouseEnter(trigger)
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(screen.queryByTestId("node-type-tooltip")).not.toBeInTheDocument()
    // Drag ends; a fresh deliberate hover opens normally.
    fireEvent.pointerUp(window)
    fireEvent.mouseEnter(trigger)
    act(() => {
      vi.advanceTimersByTime(DELAY)
    })
    expect(screen.getByTestId("node-type-tooltip")).toBeInTheDocument()
  })

  it("uses a custom testId when provided", () => {
    renderTooltip({ testId: "my-tip" })
    fireEvent.focus(screen.getByTestId("tooltip-trigger"))
    expect(screen.getByTestId("my-tip")).toBeInTheDocument()
  })
})
