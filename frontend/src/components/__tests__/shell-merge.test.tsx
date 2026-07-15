/**
 * Phase 2 Package 2D-3 — Shell trinity merge (item #69).
 *
 * Regression guards for merging:
 *   - frontend/src/panels/PanelShell.tsx     (resizable panel wrapper)
 *   - frontend/src/panels/PanelHeader.tsx    (panel header + close button)
 *   - frontend/src/components/ModalShell.tsx (modal shell w/ focus trap)
 *
 * Reviewer direction: merge PanelShell + PanelHeader into one `ResizablePanel`.
 * Keep ModalShell if its behavior (focus trap, Escape, backdrop click) is
 * distinct enough to warrant its own component.
 *
 * These tests pin the behavior that must NOT regress, regardless of which
 * internal refactor the dev picks:
 *
 *   1. A panel with a title renders the title text.
 *   2. The close button fires `onClose`.
 *   3. A left-edge drag handle fires a resize event that persists to the store.
 *   4. Children render unchanged inside the panel.
 *   5. ModalShell's Phase 1 #41 focus trap (Tab wrap, Escape close, never leak
 *      focus outside the dialog) still works.
 *   6. Consumers (ImportsPanel) that used PanelShell + PanelHeader still
 *      render identically after the merge.
 *
 * Separate test files that already cover sub-parts and MUST continue passing:
 *   - frontend/src/panels/__tests__/PanelShell.test.tsx
 *       → width math, slide-in class, min/max clamp, store persistence.
 *   - frontend/src/panels/__tests__/PanelHeader.test.tsx
 *       → title/icon/subtitle/actions rendering variants.
 *   - frontend/src/components/__tests__/ModalShell.test.tsx
 *       → backdrop click, aria attrs, extra close keys, width class, unmount.
 *   - frontend/src/components/__tests__/ModalShell.focusTrap.test.tsx
 *       → outside-focus redirect, Tab wrap, single/zero focusables.
 *
 * The tests below deliberately overlap some of those at the smallest possible
 * surface (title + close + drag + children + focus + Escape) so that if the
 * dev drops either of the Panel* components entirely, this file still enforces
 * the merged API meets the same contract.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import PanelShell from "../../panels/PanelShell"
import PanelHeader from "../../panels/PanelHeader"
import ModalShell from "../ModalShell"
import ImportsPanel from "../../panels/ImportsPanel"
import useUIStore from "../../stores/useUIStore"

// Mock the heavy CodeMirror-based CodeEditor used by ImportsPanel so the
// consumer test doesn't load the real editor.
vi.mock("../../panels/editors", () => ({
  CodeEditor: ({
    defaultValue,
    onChange,
    placeholder,
  }: {
    defaultValue?: string
    onChange?: (v: string) => void
    placeholder?: string
  }) => (
    <textarea
      data-testid="code-editor"
      defaultValue={defaultValue}
      onChange={(e) => onChange?.(e.target.value)}
      placeholder={placeholder}
    />
  ),
}))

beforeEach(() => {
  // Realistic viewport for PanelShell width math.
  Object.defineProperty(window, "innerWidth", {
    value: 1920,
    writable: true,
    configurable: true,
  })
  useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true })
})

afterEach(cleanup)

afterEach(() => {
  // The "Tab from OUTSIDE the modal" test writes a bare #outside button
  // directly into document.body; RTL's cleanup() only unmounts React trees,
  // so remove it here to stop it leaking into later tests.
  document.getElementById("outside")?.remove()
})

describe("Phase 2D-3 shell merge — panel surface preserved", () => {
  it("panel with a title string renders the title", () => {
    // A panel built by wrapping PanelHeader inside PanelShell (the current
    // pattern in ImportsPanel/GitPanel/UtilityPanel) must render its title.
    // After the merge to `ResizablePanel`, the equivalent call — whether a
    // `title` prop, `<PanelHeader>` child, or JSX composition — must still
    // render the title text verbatim.
    render(
      <PanelShell>
        <PanelHeader title="Trace" onClose={vi.fn()} />
      </PanelShell>,
    )
    expect(screen.getByText("Trace")).toBeInTheDocument()
  })

  it("close button fires onClose exactly once", () => {
    const onClose = vi.fn()
    render(
      <PanelShell>
        <PanelHeader title="Any" onClose={onClose} />
      </PanelShell>,
    )
    fireEvent.click(screen.getByTitle("Close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("drag-to-resize handle persists new width to the UI store", () => {
    // PanelShell exposes a `cursor-col-resize` handle at the left edge.
    // Dragging it left grows the panel; the final width is saved to
    // useUIStore.nodePanelWidth on mouseup.  After the merge, whether the
    // handle lives in a ResizablePanel wrapper or a dedicated hook, this
    // store-persistence contract must be preserved so width survives
    // panel toggles.
    useUIStore.setState({ nodePanelWidth: 500 })
    const { container } = render(
      <PanelShell>
        <span>content</span>
      </PanelShell>,
    )
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement
    expect(handle).toBeTruthy()

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 300 }) // delta = 100 → 500 + 100 = 600
    fireEvent.mouseUp(window)

    expect(useUIStore.getState().nodePanelWidth).toBe(600)
  })

  it("panel renders arbitrary children unchanged", () => {
    // Any node-shaped content (HTML, text, nested components) placed inside
    // the panel must appear in the DOM unchanged by the wrapper.
    render(
      <PanelShell>
        <div data-testid="arbitrary-child">
          <strong>Bold</strong> and <em>italic</em>
        </div>
      </PanelShell>,
    )
    const child = screen.getByTestId("arbitrary-child")
    expect(child).toBeInTheDocument()
    expect(child.querySelector("strong")?.textContent).toBe("Bold")
    expect(child.querySelector("em")?.textContent).toBe("italic")
  })
})

describe("Phase 2D-3 shell merge — ModalShell focus trap (#41) preserved", () => {
  it("Tab at the last focusable wraps to the first focusable", () => {
    // Regression guard on the pre-existing Phase 1 #41 fix: focus trap must
    // survive the 2D-3 merge even if internal plumbing changes.  Without this,
    // a user tabbing through a modal can escape into the background.
    render(
      <ModalShell ariaLabel="Trap test" onClose={vi.fn()}>
        <button>First</button>
        <input type="text" defaultValue="mid" />
        <button>Last</button>
      </ModalShell>,
    )
    const last = screen.getByText("Last")
    last.focus()
    expect(document.activeElement).toBe(last)

    fireEvent.keyDown(document, { key: "Tab" })

    expect(document.activeElement).toBe(screen.getByText("First"))
  })

  it("Shift+Tab at the first focusable wraps to the last focusable", () => {
    render(
      <ModalShell ariaLabel="Trap test" onClose={vi.fn()}>
        <button>First</button>
        <button>Last</button>
      </ModalShell>,
    )
    const first = screen.getByText("First")
    first.focus()
    expect(document.activeElement).toBe(first)

    fireEvent.keyDown(document, { key: "Tab", shiftKey: true })

    expect(document.activeElement).toBe(screen.getByText("Last"))
  })

  it("Escape fires onClose while a focusable inside the modal is focused", () => {
    // Escape must close the modal regardless of internal focus location.
    // Guards against any accidental preventDefault/stopPropagation added
    // during the merge that could swallow the Escape key.
    const onClose = vi.fn()
    render(
      <ModalShell ariaLabel="Esc test" onClose={onClose}>
        <button>Only</button>
      </ModalShell>,
    )
    screen.getByText("Only").focus()
    fireEvent.keyDown(document, { key: "Escape" })
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("Tab from an element OUTSIDE the modal redirects focus back INSIDE", () => {
    // Phase 1 #41 fix: if focus somehow lands on a background element, Tab
    // must not let focus walk through the rest of the page.  This is the
    // most common focus-trap leak and is non-obvious to re-implement after
    // a refactor.
    document.body.innerHTML = '<button id="outside">Outside</button>'
    const outside = document.getElementById("outside") as HTMLButtonElement

    render(
      <ModalShell ariaLabel="Outside test" onClose={vi.fn()}>
        <button>A</button>
        <button>B</button>
      </ModalShell>,
    )

    outside.focus()
    expect(document.activeElement).toBe(outside)

    fireEvent.keyDown(document, { key: "Tab" })

    const modal = screen.getByRole("dialog")
    expect(modal.contains(document.activeElement)).toBe(true)
  })
})

describe("Phase 2D-3 shell merge — consumer regression guards", () => {
  it("ImportsPanel still renders its title, description, and close button after any PanelShell/Header merge", () => {
    // ImportsPanel is the smallest consumer of PanelShell + PanelHeader and
    // touches every surface being merged (title, close, subtitle, panel
    // wrapper).  If the dev merges the two into `ResizablePanel` and updates
    // this caller, the rendered output must be identical to a user.
    render(
      <ImportsPanel
        preamble="from utility.features import *"
        onPreambleChange={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    expect(screen.getByText("Pipeline Imports")).toBeInTheDocument()
    expect(
      screen.getByText(/Import statements for utility modules/),
    ).toBeInTheDocument()
    expect(screen.getByTitle("Close")).toBeInTheDocument()
  })

  it("ImportsPanel's close button still propagates onClose after any merge", () => {
    // Proves the close-button wiring survives whatever wrapper-component
    // shape the merged API ends up with.  If this fails, clicking "X" in
    // the real app will no longer close panels.
    const onClose = vi.fn()
    render(
      <ImportsPanel
        preamble=""
        onPreambleChange={vi.fn()}
        onClose={onClose}
      />,
    )
    fireEvent.click(screen.getByTitle("Close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("ImportsPanel's outer wrapper still exposes a resize handle", () => {
    // Whether the wrapper is still called PanelShell or has been renamed to
    // ResizablePanel, the rendered DOM must include a drag handle with the
    // cursor-col-resize class so users can resize the panel.
    const { container } = render(
      <ImportsPanel
        preamble=""
        onPreambleChange={vi.fn()}
        onClose={vi.fn()}
      />,
    )
    const handle = container.querySelector(".cursor-col-resize")
    expect(handle).toBeTruthy()
  })
})
