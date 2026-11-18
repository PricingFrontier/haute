/**
 * Tests for KeyboardShortcuts modal component.
 *
 * Tests: heading rendering, shortcut label display, and all four
 * dismiss mechanisms (Escape, "?", backdrop click, close button).
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import KeyboardShortcuts from "../../components/KeyboardShortcuts"

afterEach(cleanup)

// ── Tests ────────────────────────────────────────────────────────

describe("KeyboardShortcuts", () => {
  it("renders the 'Keyboard Shortcuts' heading", () => {
    render(<KeyboardShortcuts onClose={vi.fn()} />)

    expect(screen.getByText("Keyboard Shortcuts")).toBeTruthy()
    expect(screen.getByText("Keyboard Shortcuts").tagName).toBe("H2")
  })

  it("renders all shortcut labels from KEYBOARD_SHORTCUT_BINDINGS", () => {
    render(<KeyboardShortcuts onClose={vi.fn()} />)

    const expectedLabels = [
      "Undo",
      "Redo",
      "Copy selected nodes",
      "Paste nodes",
      "Select all nodes",
      "Group into submodel",
      "Save pipeline",
      "Search nodes",
      "Fit view",
      "Refresh the open preview",
      "Delete selected",
      "Rename the selected node",
      "Close panel / clear trace",
      "Show this help",
    ]

    for (const label of expectedLabels) {
      expect(screen.getByText(label)).toBeTruthy()
    }
  })

  it("calls onClose when Escape key is pressed", () => {
    const onClose = vi.fn()
    render(<KeyboardShortcuts onClose={onClose} />)

    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("calls onClose when '?' key is pressed", () => {
    const onClose = vi.fn()
    render(<KeyboardShortcuts onClose={onClose} />)

    fireEvent.keyDown(document, { key: "?" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("calls onClose when clicking the backdrop", () => {
    const onClose = vi.fn()
    render(<KeyboardShortcuts onClose={onClose} />)

    // The backdrop is the outermost div with role="dialog"
    const backdrop = screen.getByRole("dialog")
    fireEvent.click(backdrop)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("does not call onClose when clicking inside the modal content", () => {
    const onClose = vi.fn()
    render(<KeyboardShortcuts onClose={onClose} />)

    // Click the heading text inside the modal — should NOT trigger backdrop close
    fireEvent.click(screen.getByText("Keyboard Shortcuts"))
    expect(onClose).not.toHaveBeenCalled()
  })

  it("calls onClose when clicking the close button", () => {
    const onClose = vi.fn()
    render(<KeyboardShortcuts onClose={onClose} />)

    const closeButton = screen.getByLabelText("Close keyboard shortcuts")
    fireEvent.click(closeButton)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("renders the dialog with correct aria attributes", () => {
    render(<KeyboardShortcuts onClose={vi.fn()} />)

    const dialog = screen.getByRole("dialog")
    expect(dialog.getAttribute("aria-modal")).toBe("true")
    expect(dialog.getAttribute("aria-label")).toBe("Keyboard shortcuts")
  })
})
