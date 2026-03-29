import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ModalShell from "../ModalShell"

afterEach(cleanup)

function renderShell(overrides: Partial<Parameters<typeof ModalShell>[0]> = {}) {
  const props = {
    ariaLabel: "Test dialog",
    onClose: vi.fn(),
    children: <p>Modal content</p>,
    ...overrides,
  }
  return { ...render(<ModalShell {...props} />), props }
}

describe("ModalShell", () => {
  it("renders children", () => {
    renderShell()
    expect(screen.getByText("Modal content")).toBeInTheDocument()
  })

  it("sets correct aria attributes", () => {
    renderShell({ ariaLabel: "My dialog" })
    const dialog = screen.getByRole("dialog")
    expect(dialog).toHaveAttribute("aria-modal", "true")
    expect(dialog).toHaveAttribute("aria-label", "My dialog")
  })

  it("applies default width class (w-[360px])", () => {
    renderShell()
    // The inner panel should have the width class
    const panel = screen.getByText("Modal content").closest(".w-\\[360px\\]")
    expect(panel).toBeTruthy()
  })

  it("applies custom width class", () => {
    renderShell({ width: "w-[400px]" })
    const panel = screen.getByText("Modal content").closest(".w-\\[400px\\]")
    expect(panel).toBeTruthy()
  })

  it("calls onClose when clicking the backdrop", () => {
    const { props } = renderShell()
    const backdrop = screen.getByRole("dialog")
    fireEvent.click(backdrop)
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("does NOT call onClose when clicking inside the panel", () => {
    const { props } = renderShell()
    fireEvent.click(screen.getByText("Modal content"))
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("calls onClose on Escape key", () => {
    const { props } = renderShell()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("does NOT call onClose on other keys by default", () => {
    const { props } = renderShell()
    fireEvent.keyDown(document, { key: "Enter" })
    fireEvent.keyDown(document, { key: "a" })
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("calls onClose on extraCloseKeys", () => {
    const { props } = renderShell({ extraCloseKeys: ["?"] })
    fireEvent.keyDown(document, { key: "?" })
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("cleans up event listeners on unmount", () => {
    const { props, unmount } = renderShell()
    unmount()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).not.toHaveBeenCalled()
  })

  it("focus trap: Tab wraps from last to first focusable element", () => {
    renderShell({
      children: (
        <>
          <button>First</button>
          <button>Last</button>
        </>
      ),
    })
    const lastBtn = screen.getByText("Last")
    lastBtn.focus()
    expect(document.activeElement).toBe(lastBtn)
    fireEvent.keyDown(document, { key: "Tab" })
    expect(document.activeElement).toBe(screen.getByText("First"))
  })

  it("focus trap: Shift+Tab wraps from first to last focusable element", () => {
    renderShell({
      children: (
        <>
          <button>First</button>
          <button>Last</button>
        </>
      ),
    })
    const firstBtn = screen.getByText("First")
    firstBtn.focus()
    expect(document.activeElement).toBe(firstBtn)
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(screen.getByText("Last"))
  })
})
