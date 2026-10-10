/**
 * The drag-reorder hook the step editor's cards and the workbench's field order share: a
 * row picked up and dropped on another row is put there.
 */
import { afterEach, describe, expect, it, vi } from "vitest"
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react"
import useListReorder from "../useListReorder"

function Rows({ move }: { move: (from: number, to: number) => void }) {
  const dragFor = useListReorder(move)
  return (
    <ul>
      {["a", "b", "c"].map((name, index) => {
        const drag = dragFor(index)
        return (
          <li
            key={name}
            data-testid={`row-${name}`}
            data-target={drag.target ? "true" : undefined}
            data-dragging={drag.dragging ? "true" : undefined}
            draggable
            onDragStart={drag.onStart}
            onDragOver={drag.onOver}
            onDrop={drag.onDrop}
            onDragEnd={drag.onEnd}
          >
            {name}
          </li>
        )
      })}
    </ul>
  )
}

const dataTransfer = () => ({ setData: vi.fn(), effectAllowed: "", dropEffect: "" })

describe("useListReorder", () => {
  afterEach(cleanup)

  it("puts the dragged row where it is dropped, marking the row held over meanwhile", () => {
    const move = vi.fn()
    render(<Rows move={move} />)

    act(() => {
      fireEvent.dragStart(screen.getByTestId("row-a"), { dataTransfer: dataTransfer() })
    })
    expect(screen.getByTestId("row-a")).toHaveAttribute("data-dragging", "true")
    act(() => {
      fireEvent.dragOver(screen.getByTestId("row-c"), { dataTransfer: dataTransfer() })
    })
    expect(screen.getByTestId("row-c")).toHaveAttribute("data-target", "true")
    expect(screen.getByTestId("row-a")).not.toHaveAttribute("data-target")

    act(() => {
      fireEvent.drop(screen.getByTestId("row-c"), { dataTransfer: dataTransfer() })
    })
    expect(move).toHaveBeenCalledWith(0, 2)
    expect(screen.getByTestId("row-c")).not.toHaveAttribute("data-target")
    expect(screen.getByTestId("row-a")).not.toHaveAttribute("data-dragging")
  })

  it("ignores a drag-over from outside the list, and a drag that ends without a drop moves nothing", () => {
    const move = vi.fn()
    render(<Rows move={move} />)

    act(() => {
      fireEvent.dragOver(screen.getByTestId("row-b"), { dataTransfer: dataTransfer() })
    })
    expect(screen.getByTestId("row-b")).not.toHaveAttribute("data-target")

    act(() => {
      fireEvent.dragStart(screen.getByTestId("row-b"), { dataTransfer: dataTransfer() })
      fireEvent.dragEnd(screen.getByTestId("row-b"))
    })
    expect(move).not.toHaveBeenCalled()
    expect(screen.getByTestId("row-b")).not.toHaveAttribute("data-dragging")
  })
})
