/**
 * Tests for useClickOutside hook.
 *
 * Tests: listener activation, click inside vs outside, cleanup on deactivate.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { useRef, createElement } from "react"
import useClickOutside from "../../hooks/useClickOutside"

afterEach(cleanup)

function TestComponent({ onClose, active }: { onClose: () => void; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, onClose, active)
  return createElement("div", null,
    // eslint-disable-next-line react-hooks/refs -- ref prop on DOM element is standard React usage
    createElement("div", { ref, "data-testid": "inside" }, "Inside"),
    createElement("div", { "data-testid": "outside" }, "Outside"),
  )
}

describe("useClickOutside", () => {
  it("calls onClose when clicking outside and active=true", () => {
    const onClose = vi.fn()
    render(createElement(TestComponent, { onClose, active: true }))
    fireEvent.pointerDown(screen.getByTestId("outside"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("does NOT call onClose when clicking inside and active=true", () => {
    const onClose = vi.fn()
    render(createElement(TestComponent, { onClose, active: true }))
    fireEvent.pointerDown(screen.getByTestId("inside"))
    expect(onClose).not.toHaveBeenCalled()
  })

  it("closes on a press outside whose own handler cancels the mouse events after it and stops it, as a sheet component's does", () => {
    const onClose = vi.fn()
    render(createElement(TestComponent, { onClose, active: true }))
    const outside = screen.getByTestId("outside")
    // The press is all there is: no mousedown follows a pointerdown whose default is prevented.
    outside.addEventListener("pointerdown", (event) => {
      event.preventDefault()
      event.stopPropagation()
    })
    fireEvent.pointerDown(outside)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("does NOT call onClose when clicking outside and active=false", () => {
    const onClose = vi.fn()
    render(createElement(TestComponent, { onClose, active: false }))
    fireEvent.pointerDown(screen.getByTestId("outside"))
    expect(onClose).not.toHaveBeenCalled()
  })
})
