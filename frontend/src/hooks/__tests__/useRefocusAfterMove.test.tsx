/**
 * The focus put back on an item a keyboard move redrew in another place of its list
 * (specs/frontend-shared): moving an element in the page drops its focus.
 */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import useRefocusAfterMove, { altArrowMove } from "../useRefocusAfterMove"

function List({ order, onMove }: { order: string[]; onMove: (moved: (key: string) => void) => void }) {
  const refocus = useRefocusAfterMove<HTMLButtonElement>()
  onMove(refocus.moved)
  return (
    <div>
      {order.map((key) => (
        <button key={key} ref={refocus.register(key)} type="button">
          {key}
        </button>
      ))}
    </div>
  )
}

describe("useRefocusAfterMove", () => {
  afterEach(cleanup)

  it("focuses the item noted as moved once the list is drawn again, and nothing after", () => {
    let moved: (key: string) => void = () => undefined
    const { rerender } = render(<List order={["a", "b", "c"]} onMove={(note) => { moved = note }} />)
    screen.getByText("c").focus()
    document.body.focus()

    moved("a")
    rerender(<List order={["b", "a", "c"]} onMove={(note) => { moved = note }} />)
    expect(document.activeElement).toBe(screen.getByText("a"))

    // Drawn again with nothing moved, the focus stays where it is.
    screen.getByText("c").focus()
    rerender(<List order={["b", "a", "c"]} onMove={(note) => { moved = note }} />)
    expect(document.activeElement).toBe(screen.getByText("c"))
  })

  it("moves an item a place with Alt and the arrows of its axis, never past either end, and leaves other keys alone", () => {
    const key = (key: string, altKey = true) => {
      const event = { key, altKey, defaultPrevented: false, preventDefault() { this.defaultPrevented = true } }
      return event as unknown as Parameters<typeof altArrowMove>[0] & { defaultPrevented: boolean }
    }
    expect(altArrowMove(key("ArrowDown"), "vertical", 1, 3)).toBe(2)
    expect(altArrowMove(key("ArrowUp"), "vertical", 1, 3)).toBe(0)
    expect(altArrowMove(key("ArrowLeft"), "horizontal", 1, 3)).toBe(0)
    // Past an end: nothing, the key's default still prevented.
    const atEnd = key("ArrowDown")
    expect(altArrowMove(atEnd, "vertical", 2, 3)).toBeNull()
    expect(atEnd.defaultPrevented).toBe(true)
    // Another axis's arrow, or no Alt: not the move's key, left alone.
    const across = key("ArrowRight")
    expect(altArrowMove(across, "vertical", 1, 3)).toBeNull()
    expect(across.defaultPrevented).toBe(false)
    expect(altArrowMove(key("ArrowDown", false), "vertical", 1, 3)).toBeNull()
  })
})
