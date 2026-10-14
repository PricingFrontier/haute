import { describe, it, expect, afterEach } from "vitest"
import { cleanup, render } from "@testing-library/react"
import useWindowFileDropGuard from "../useWindowFileDropGuard"

function Guarded({ takesFiles }: { takesFiles: boolean }) {
  useWindowFileDropGuard()
  return (
    <div
      data-testid="target"
      onDragOver={takesFiles ? (event) => event.preventDefault() : undefined}
      onDrop={takesFiles ? (event) => event.preventDefault() : undefined}
    />
  )
}

function dragEvent(type: "dragover" | "drop", types: string[]) {
  const event = new Event(type, { bubbles: true, cancelable: true }) as DragEvent
  Object.defineProperty(event, "dataTransfer", { value: { types, dropEffect: "copy" } })
  return event
}

describe("useWindowFileDropGuard", () => {
  afterEach(cleanup)

  it("prevents a file drop nothing handled, so the browser does not open the file", () => {
    const { getByTestId } = render(<Guarded takesFiles={false} />)
    const over = dragEvent("dragover", ["Files"])
    const drop = dragEvent("drop", ["Files"])

    getByTestId("target").dispatchEvent(over)
    getByTestId("target").dispatchEvent(drop)

    expect(over.defaultPrevented).toBe(true)
    expect(over.dataTransfer?.dropEffect).toBe("none")
    expect(drop.defaultPrevented).toBe(true)
  })

  it("leaves a drop a target handled, and drags that carry no files, alone", () => {
    const { getByTestId } = render(<Guarded takesFiles />)
    const handled = dragEvent("dragover", ["Files"])
    getByTestId("target").dispatchEvent(handled)
    expect(handled.dataTransfer?.dropEffect).toBe("copy")

    cleanup()
    const { getByTestId: getOther } = render(<Guarded takesFiles={false} />)
    const text = dragEvent("dragover", ["text/plain"])
    getOther("target").dispatchEvent(text)
    expect(text.defaultPrevented).toBe(false)
  })
})
