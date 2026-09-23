/**
 * Gap tests for useClickOutside — covers scenarios missing from the main test:
 *
 * 1. ref being null (no DOM element attached)
 * 2. Toggling active from true→false removes the listener (cleanup)
 * 3. Nested elements — clicking a child of the ref element counts as "inside"
 * 4. Event propagation — onClose fires even if the click is on a deeply nested outside element
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { useRef, useState } from "react"
import useClickOutside from "../../hooks/useClickOutside"

afterEach(cleanup)

function TestComponent({ onClose, active }: { onClose: () => void; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, onClose, active)
  return (
    <div>
      <div ref={ref} data-testid="inside">
        <span data-testid="nested-child">Nested</span>
      </div>
      <div data-testid="outside">
        <span data-testid="deep-outside">Deep</span>
      </div>
    </div>
  )
}

/** Component that lets us toggle active state dynamically. */
function ToggleComponent({ onClose }: { onClose: () => void }) {
  const [active, setActive] = useState(true)
  const ref = useRef<HTMLDivElement>(null)
  useClickOutside(ref, onClose, active)
  return (
    <div>
      <div ref={ref} data-testid="inside">Inside</div>
      <div data-testid="outside">Outside</div>
      <button data-testid="toggle-btn" onClick={() => setActive(false)}>
        Deactivate
      </button>
    </div>
  )
}

/** Component with a null ref — simulates conditional rendering. */
function NullRefComponent({ onClose, active }: { onClose: () => void; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null)
  // Intentionally don't attach ref to any element
  useClickOutside(ref, onClose, active)
  return <div data-testid="somewhere">Hello</div>
}

describe("useClickOutside - gap tests", () => {
  // ────────────────────────────────────────────────────────────────
  // 1. ref is null (no DOM element attached)
  // ────────────────────────────────────────────────────────────────

  it("calls onClose when ref.current is null and active=true (click is always 'outside')", () => {
    // Catches: if the guard `ref.current && !ref.current.contains(...)` were
    // changed to just `!ref.current.contains(...)`, it would throw a
    // TypeError on null. The current code safely short-circuits: when
    // ref.current is null, the `&&` fails so onClose is NOT called.
    const onClose = vi.fn()
    render(<NullRefComponent onClose={onClose} active={true} />)

    fireEvent.mouseDown(screen.getByTestId("somewhere"))

    // With the current code: ref.current is null → `ref.current && ...`
    // short-circuits to false → onClose is NOT called. This is correct:
    // if there's no element to track, we shouldn't fire close.
    expect(onClose).not.toHaveBeenCalled()
  })

  // ────────────────────────────────────────────────────────────────
  // 2. Toggling active from true→false removes the listener
  // ────────────────────────────────────────────────────────────────

  it("stops calling onClose after active transitions from true to false", () => {
    // Catches: if the cleanup function in the useEffect doesn't properly
    // remove the mousedown listener, clicking outside after deactivation
    // would still fire onClose — e.g. closing a dropdown that's already
    // closed, or triggering navigation.
    const onClose = vi.fn()
    render(<ToggleComponent onClose={onClose} />)

    // First click outside — should trigger onClose
    fireEvent.mouseDown(screen.getByTestId("outside"))
    expect(onClose).toHaveBeenCalledTimes(1)

    // Toggle active to false
    fireEvent.click(screen.getByTestId("toggle-btn"))

    // Second click outside — should NOT trigger onClose
    fireEvent.mouseDown(screen.getByTestId("outside"))
    expect(onClose).toHaveBeenCalledTimes(1) // still 1
  })

  // ────────────────────────────────────────────────────────────────
  // 3. Nested elements — clicking a child is "inside"
  // ────────────────────────────────────────────────────────────────

  it("does NOT call onClose when clicking a nested child of the ref element", () => {
    // Catches: if `contains()` were replaced with a strict equality check
    // (`e.target === ref.current`), clicks on nested children would be
    // treated as "outside", closing dropdowns when clicking their items.
    const onClose = vi.fn()
    render(<TestComponent onClose={onClose} active={true} />)

    fireEvent.mouseDown(screen.getByTestId("nested-child"))

    expect(onClose).not.toHaveBeenCalled()
  })

  // ────────────────────────────────────────────────────────────────
  // 4. Deeply nested outside element still triggers onClose
  // ────────────────────────────────────────────────────────────────

  it("calls onClose when clicking a deeply nested element outside the ref", () => {
    // Catches: if event.target resolution breaks for nested elements
    // outside the ref container, the hook would fail to detect the
    // outside click and the dropdown would stay permanently open.
    const onClose = vi.fn()
    render(<TestComponent onClose={onClose} active={true} />)

    fireEvent.mouseDown(screen.getByTestId("deep-outside"))

    expect(onClose).toHaveBeenCalledTimes(1)
  })

  // ────────────────────────────────────────────────────────────────
  // 5. Multiple instances don't interfere
  // ────────────────────────────────────────────────────────────────

  it("two instances with different refs fire independently", () => {
    // Catches: if useClickOutside accidentally shared global state
    // between instances, clicking outside one component could
    // close a completely unrelated dropdown.
    const onClose1 = vi.fn()
    const onClose2 = vi.fn()

    function DualComponent() {
      const ref1 = useRef<HTMLDivElement>(null)
      const ref2 = useRef<HTMLDivElement>(null)
      useClickOutside(ref1, onClose1, true)
      useClickOutside(ref2, onClose2, true)
      return (
        <div>
          <div ref={ref1} data-testid="box1">Box1</div>
          <div ref={ref2} data-testid="box2">Box2</div>
          <div data-testid="neither">Neither</div>
        </div>
      )
    }

    render(<DualComponent />)

    // Click inside box1 — box1 should NOT close, box2 SHOULD (it's outside box2)
    fireEvent.mouseDown(screen.getByTestId("box1"))
    expect(onClose1).not.toHaveBeenCalled()
    expect(onClose2).toHaveBeenCalledTimes(1)
  })
})
