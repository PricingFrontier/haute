import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { SidePanelHeader } from "../SidePanel"

afterEach(cleanup)

describe("SidePanelHeader", () => {
  it("renders string title", () => {
    render(<SidePanelHeader title="Test Panel" onClose={vi.fn()} />)
    expect(screen.getByText("Test Panel")).toBeInTheDocument()
  })

  it("renders ReactNode title", () => {
    render(<SidePanelHeader title={<div data-testid="custom-title">Custom</div>} onClose={vi.fn()} />)
    expect(screen.getByTestId("custom-title")).toBeInTheDocument()
  })

  it("renders close button with Close title", () => {
    render(<SidePanelHeader title="Test" onClose={vi.fn()} />)
    expect(screen.getByTitle("Close")).toBeInTheDocument()
  })

  it("clicking close button calls onClose", () => {
    const onClose = vi.fn()
    render(<SidePanelHeader title="Test" onClose={onClose} />)
    fireEvent.click(screen.getByTitle("Close"))
    expect(onClose).toHaveBeenCalledTimes(1)
  })

  it("renders icon when provided", () => {
    render(<SidePanelHeader title="Test" onClose={vi.fn()} icon={<span data-testid="icon">I</span>} />)
    expect(screen.getByTestId("icon")).toBeInTheDocument()
  })

  it("renders subtitle when provided", () => {
    render(<SidePanelHeader title="Test" onClose={vi.fn()} subtitle={<span>Subtitle text</span>} />)
    expect(screen.getByText("Subtitle text")).toBeInTheDocument()
  })

  it("renders actions when provided", () => {
    render(<SidePanelHeader title="Test" onClose={vi.fn()} actions={<button>Action</button>} />)
    expect(screen.getByText("Action")).toBeInTheDocument()
  })

  it("does not render icon/subtitle/actions when not provided", () => {
    const { container } = render(<SidePanelHeader title="Test" onClose={vi.fn()} />)
    // Only the title text and close button should be meaningful children
    expect(container.querySelectorAll("button").length).toBe(1) // just close
  })
})
