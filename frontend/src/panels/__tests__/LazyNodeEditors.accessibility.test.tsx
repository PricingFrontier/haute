import { lazy } from "react"
import { render, screen, cleanup } from "@testing-library/react"
import { afterEach, describe, expect, it } from "vitest"
import { LazyEditorBoundary } from "../LazyNodeEditors"

const SuspendedEditor = lazy(() => new Promise<{ default: () => null }>(() => {}))

afterEach(cleanup)

describe("LazyEditorBoundary accessibility", () => {
  it("announces lazy editor loading state", () => {
    render(
      <LazyEditorBoundary>
        <SuspendedEditor />
      </LazyEditorBoundary>,
    )

    const status = screen.getByRole("status", { name: /loading editor/i })
    expect(status).toHaveAttribute("aria-live", "polite")
    expect(screen.getByTestId("editor-loading")).toBeInTheDocument()
  })
})
