/**
 * The focus put back on an item a keyboard move redrew in another place of its list
 * (specs/frontend-shared): moving an element in the page drops its focus.
 */
import { cleanup, render, screen } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import useRefocusAfterMove from "../useRefocusAfterMove"

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
})
