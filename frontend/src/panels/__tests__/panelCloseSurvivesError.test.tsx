/**
 * Invariant guard for the "hard-to-remove banner" bug class (BUGS.md): no error
 * state may remove a dismissible panel's only close control. The fix is
 * structural — a panel's close button must be a SIBLING of, never a child of,
 * the ErrorBoundary that wraps its body (so a thrown render — e.g. a lazy editor
 * chunk that 404s on a stale build — surfaces the fallback INSIDE the body while
 * the header/close stay rendered).
 *
 * This pins the shared `PanelHeader` chrome (UtilityPanel/ImportsPanel/GitPanel/
 * TracePanel); NodePanel's own header is covered by NodePanel.editorCrash.test.
 */
import { describe, it, expect, vi, afterEach, beforeEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { ErrorBoundary } from "../../components/ErrorBoundary"
import PanelHeader from "../PanelHeader"

function Boom(): never {
  throw new Error("error loading dynamically imported module: /assets/Editor-DEAD.js")
}

describe("panel close survives a body error", () => {
  beforeEach(() => {
    // ErrorBoundary.componentDidCatch logs the caught error; silence it.
    vi.spyOn(console, "error").mockImplementation(() => {})
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it("keeps the close button (and fires onClose) when the body throws", () => {
    const onClose = vi.fn()
    render(
      // The correct structure: header (close) is a sibling of the body boundary.
      <div>
        <PanelHeader title="Demo Panel" onClose={onClose} />
        <ErrorBoundary name="Body">
          <Boom />
        </ErrorBoundary>
      </div>,
    )
    // Boundary caught the throw and rendered its MARKED fallback…
    expect(screen.getByTestId("error-boundary-fallback")).toBeInTheDocument()
    expect(screen.getByText("Something went wrong")).toBeInTheDocument()
    // …and the close control survived and works.
    const close = screen.getByTestId("panel-close")
    expect(close).toBeInTheDocument()
    fireEvent.click(close)
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("documents the anti-pattern: a boundary that wraps the header eats the close", () => {
    // The original bug. If the close lives INSIDE the boundary, a body error
    // removes it — this test pins that the boundary genuinely replaces its whole
    // subtree, so close MUST be placed as a sibling (the test above).
    const onClose = vi.fn()
    render(
      <ErrorBoundary name="WholePanel">
        <PanelHeader title="Demo Panel" onClose={onClose} />
        <Boom />
      </ErrorBoundary>,
    )
    expect(screen.getByTestId("error-boundary-fallback")).toBeInTheDocument()
    expect(screen.queryByTestId("panel-close")).not.toBeInTheDocument()
  })
})
