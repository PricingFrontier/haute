import { describe, it, expect, beforeEach, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import PanelShell from "../PanelShell"
import useUIStore from "../../stores/useUIStore"

beforeEach(() => {
  // Simulate a 1920px-wide window so dynamic sizing is realistic
  Object.defineProperty(window, "innerWidth", { value: 1920, writable: true, configurable: true })
  useUIStore.setState({ nodePanelWidth: 600, paletteOpen: true })
})

afterEach(cleanup)

describe("PanelShell", () => {
  it("renders children", () => {
    render(<PanelShell><span data-testid="child">Hello</span></PanelShell>)
    expect(screen.getByTestId("child")).toBeTruthy()
  })

  it("uses panel width from store when explicitly set", () => {
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const shell = container.firstElementChild as HTMLElement
    expect(shell.style.width).toBe("600px")
  })

  it("uses 50% of available space when store value is 0 (default)", () => {
    useUIStore.setState({ nodePanelWidth: 0 })
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const shell = container.firstElementChild as HTMLElement
    // Available = 1920 - 180 (palette) = 1740; half = 870
    expect(shell.style.width).toBe("870px")
  })

  it("accounts for collapsed palette in default width", () => {
    useUIStore.setState({ nodePanelWidth: 0, paletteOpen: false })
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const shell = container.firstElementChild as HTMLElement
    // Available = 1920 - 40 (collapsed) = 1880; half = 940
    expect(shell.style.width).toBe("940px")
  })

  it("keeps its mount-time default width across unrelated rerenders", () => {
    Object.defineProperty(window, "innerWidth", { value: 1440 })
    useUIStore.setState({ nodePanelWidth: 0, paletteOpen: true })
    const { container, rerender } = render(
      <PanelShell><span>initial content</span></PanelShell>,
    )
    const shell = container.firstElementChild as HTMLElement
    expect(shell.style.width).toBe("630px")

    Object.defineProperty(window, "innerWidth", { value: 1024 })
    rerender(<PanelShell><span>updated content</span></PanelShell>)

    expect(shell.style.width).toBe("630px")
  })

  it("applies background from CSS variable", () => {
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const shell = container.firstElementChild as HTMLElement
    expect(shell.style.background).toBe("var(--bg-panel)")
  })

  it("merges additional style prop", () => {
    const { container } = render(
      <PanelShell style={{ opacity: 0.5 }}><span>content</span></PanelShell>,
    )
    const shell = container.firstElementChild as HTMLElement
    expect(shell.style.opacity).toBe("0.5")
  })

  it("has a visible drag handle with col-resize cursor", () => {
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement
    expect(handle).toBeTruthy()
  })

  it("drag handle updates panel width via mouse events", () => {
    useUIStore.setState({ nodePanelWidth: 500 })
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 300 }) // delta = 100 → 500 + 100 = 600
    fireEvent.mouseUp(window)

    expect(useUIStore.getState().nodePanelWidth).toBe(600)
  })

  it("clamps width to minimum of 320", () => {
    useUIStore.setState({ nodePanelWidth: 400 })
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: 700 }) // delta = -300 → 400 - 300 = 100 → clamped to 320
    fireEvent.mouseUp(window)

    expect(useUIStore.getState().nodePanelWidth).toBe(320)
  })

  it("clamps width to 75% of available space", () => {
    useUIStore.setState({ nodePanelWidth: 800 })
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const handle = container.querySelector(".cursor-col-resize") as HTMLElement

    fireEvent.mouseDown(handle, { clientX: 400 })
    fireEvent.mouseMove(window, { clientX: -1000 }) // delta = 1400 → 800 + 1400 = 2200 → clamped
    fireEvent.mouseUp(window)

    // Max = floor((1920 - 180) * 0.75) = 1305
    expect(useUIStore.getState().nodePanelWidth).toBe(1305)
  })

  it("has slide-in animation class", () => {
    const { container } = render(<PanelShell><span>content</span></PanelShell>)
    const shell = container.firstElementChild as HTMLElement
    expect(shell.classList.contains("animate-slide-in")).toBe(true)
  })
})
