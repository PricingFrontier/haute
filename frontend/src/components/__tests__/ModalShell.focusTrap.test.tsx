/**
 * Phase 1 Package 1H — Item #41: ModalShell focus trap must not leak focus
 * outside the dialog.
 *
 * Pre-fix: the trap only intervenes when document.activeElement is the
 * first or last focusable inside the modal.  If focus somehow lands on an
 * element OUTSIDE the modal (e.g. the page body or a background button),
 * Tab / Shift+Tab walk the entire document in the usual browser order and
 * let focus escape.
 *
 * Fix: on Tab/Shift+Tab, if activeElement is NOT inside the modal, redirect
 * to the first (or last) focusable inside the modal.  Alternative fix: use
 * a sentinel element pattern (invisible focus-trap elements at start/end)
 * that pull focus back when tabbed onto.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import ModalShell from "../ModalShell"

afterEach(cleanup)

function renderShell(overrides: Partial<Parameters<typeof ModalShell>[0]> = {}) {
  const props = {
    ariaLabel: "Focus trap test",
    onClose: vi.fn(),
    children: (
      <>
        <button>First</button>
        <input type="text" defaultValue="middle" />
        <button>Last</button>
      </>
    ),
    ...overrides,
  }
  return { ...render(<ModalShell {...props} />), props }
}

describe("ModalShell — focus trap never leaks focus outside (#41)", () => {
  it("Tab from an element OUTSIDE the modal redirects focus back inside", () => {
    // Render a background button that lives outside the modal.
    // Then focus that outside element and press Tab — focus must land
    // on the first focusable inside the modal, not escape the dialog.
    document.body.innerHTML = '<button id="outside-btn">Outside</button>'
    const outsideBtn = document.getElementById("outside-btn") as HTMLButtonElement

    renderShell()

    outsideBtn.focus()
    expect(document.activeElement).toBe(outsideBtn)

    fireEvent.keyDown(document, { key: "Tab" })

    // After Tab, focus must be inside the modal — NOT on the outside button.
    const active = document.activeElement as HTMLElement
    const modal = screen.getByRole("dialog")
    expect(modal.contains(active)).toBe(true)
  })

  it("Shift+Tab from an element OUTSIDE the modal redirects focus back inside", () => {
    document.body.innerHTML = '<button id="outside-btn">Outside</button>'
    const outsideBtn = document.getElementById("outside-btn") as HTMLButtonElement

    renderShell()

    outsideBtn.focus()
    expect(document.activeElement).toBe(outsideBtn)

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true })

    const active = document.activeElement as HTMLElement
    const modal = screen.getByRole("dialog")
    expect(modal.contains(active)).toBe(true)
  })

  it("Tab from the last focusable wraps to the first focusable (pre-existing trap)", () => {
    // Regression coverage of the existing trap behaviour.
    renderShell()
    const last = screen.getByText("Last")
    last.focus()
    expect(document.activeElement).toBe(last)
    fireEvent.keyDown(document, { key: "Tab" })
    expect(document.activeElement).toBe(screen.getByText("First"))
  })

  it("Shift+Tab from the first focusable wraps to the last focusable", () => {
    renderShell()
    const first = screen.getByText("First")
    first.focus()
    expect(document.activeElement).toBe(first)
    fireEvent.keyDown(document, { key: "Tab", shiftKey: true })
    expect(document.activeElement).toBe(screen.getByText("Last"))
  })

  it("Tab from a middle element keeps focus inside the modal", () => {
    // Baseline: Tab from a middle element should not escape. Pre-fix,
    // this works *if* activeElement is neither first nor last. We still
    // assert it to guard against regressions.
    renderShell()
    const input = screen.getByDisplayValue("middle") as HTMLInputElement
    input.focus()
    expect(document.activeElement).toBe(input)

    fireEvent.keyDown(document, { key: "Tab" })

    const active = document.activeElement as HTMLElement
    const modal = screen.getByRole("dialog")
    expect(modal.contains(active)).toBe(true)
  })

  it("Escape closes the modal regardless of where focus is", () => {
    // Focus the outside button, press Escape — modal must still close.
    // This guards against trapping Escape inside the modal-scope handler.
    document.body.innerHTML = '<button id="outside-btn">Outside</button>'
    const outsideBtn = document.getElementById("outside-btn") as HTMLButtonElement

    const { props } = renderShell()

    outsideBtn.focus()
    fireEvent.keyDown(document, { key: "Escape" })

    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("focus-trap does not interfere with Escape default cancel behaviour", () => {
    // Escape inside the modal closes normally (regression test for the
    // pre-existing behaviour).
    const { props } = renderShell()
    const first = screen.getByText("First")
    first.focus()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(props.onClose).toHaveBeenCalledTimes(1)
  })

  it("modal with only one focusable still handles Tab/Shift+Tab gracefully", () => {
    // Edge case: a modal with a single button.  Tab from it should wrap
    // back to itself (not escape).
    render(
      <ModalShell ariaLabel="Single focusable" onClose={vi.fn()}>
        <button>Only</button>
      </ModalShell>,
    )
    const only = screen.getByText("Only")
    only.focus()
    expect(document.activeElement).toBe(only)

    fireEvent.keyDown(document, { key: "Tab" })
    // Focus must stay on the same element (or at least inside the modal)
    const modal = screen.getByRole("dialog")
    const active = document.activeElement as HTMLElement
    expect(modal.contains(active)).toBe(true)
  })

  it("modal with NO focusable elements still handles Tab without throwing", () => {
    // Defensive: an unusual modal that renders only non-interactive text.
    render(
      <ModalShell ariaLabel="No focusables" onClose={vi.fn()}>
        <p>Just text</p>
      </ModalShell>,
    )
    // Tab on the document should not throw — the modal's keydown handler
    // guards against `focusable.length === 0`.
    expect(() => {
      fireEvent.keyDown(document, { key: "Tab" })
      fireEvent.keyDown(document, { key: "Tab", shiftKey: true })
    }).not.toThrow()
  })
})
