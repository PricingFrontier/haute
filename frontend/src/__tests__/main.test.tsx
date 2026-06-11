import { afterEach, describe, expect, it, vi } from "vitest"
import { StrictMode, type ReactElement } from "react"

describe("main", () => {
  afterEach(() => {
    vi.resetModules()
    vi.clearAllMocks()
    document.body.innerHTML = ""
  })

  it("mounts App inside the root ErrorBoundary", async () => {
    const render = vi.fn()
    const createRoot = vi.fn(() => ({ render }))
    const root = document.createElement("div")
    root.id = "root"
    document.body.append(root)

    vi.resetModules()
    vi.doMock("react-dom/client", () => ({ createRoot }))
    vi.doMock("../App", () => ({ default: () => <div data-testid="app" /> }))
    const { ErrorBoundary } = await import("../components/ErrorBoundary")

    await import("../main")

    expect(createRoot).toHaveBeenCalledWith(root)
    const rendered = render.mock.calls[0][0] as ReactElement<{ children: ReactElement }>
    expect(rendered.type).toBe(StrictMode)

    const boundary = rendered.props.children as ReactElement<{ children: ReactElement }>
    expect(boundary.type).toBe(ErrorBoundary)

    const app = boundary.props.children as ReactElement
    expect(app.type).toBeTypeOf("function")
  })
})
