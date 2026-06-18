import { describe, it, expect, vi, afterEach } from "vitest"
import { render, cleanup, fireEvent } from "@testing-library/react"
import useEscapeToClose from "../useEscapeToClose"

function Harness({ onClose, enabled }: { onClose: () => void; enabled?: boolean }) {
  useEscapeToClose(onClose, enabled)
  return <div data-testid="harness" />
}

describe("useEscapeToClose", () => {
  afterEach(cleanup)

  it("calls onClose on Escape while enabled", () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} />)
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("ignores other keys", () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} />)
    fireEvent.keyDown(document, { key: "Enter" })
    fireEvent.keyDown(document, { key: "a" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("does nothing when disabled", () => {
    const onClose = vi.fn()
    render(<Harness onClose={onClose} enabled={false} />)
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })

  it("stops propagation so ancestor Escape handlers do not also fire (topmost-first)", () => {
    const onClose = vi.fn()
    const ancestorHandler = vi.fn()
    // A bubble-phase document listener stands in for a panel/app Escape handler.
    document.addEventListener("keydown", ancestorHandler)
    try {
      render(<Harness onClose={onClose} />)
      fireEvent.keyDown(document, { key: "Escape" })
      expect(onClose).toHaveBeenCalledTimes(1)
      expect(ancestorHandler).not.toHaveBeenCalled()
    } finally {
      document.removeEventListener("keydown", ancestorHandler)
    }
  })

  it("removes its listener on unmount", () => {
    const onClose = vi.fn()
    const { unmount } = render(<Harness onClose={onClose} />)
    unmount()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).not.toHaveBeenCalled()
  })
})
