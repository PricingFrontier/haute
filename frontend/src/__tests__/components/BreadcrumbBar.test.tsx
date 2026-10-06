/**
 * Tests for BreadcrumbBar component.
 *
 * Tests: null rendering for short stacks, name display, chevron separators,
 * last-item disabled state, and onNavigate callback wiring.
 */
import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import BreadcrumbBar, { type ViewLevel } from "../../components/BreadcrumbBar"

afterEach(cleanup)

// ── Helpers ──────────────────────────────────────────────────────

const level = (name: string): ViewLevel => ({
  type: "pipeline",
  name,
  file: `${name.toLowerCase()}.py`,
})

const twoLevels: ViewLevel[] = [level("Main"), level("Sub")]

const threeLevels: ViewLevel[] = [
  level("Root"),
  { type: "submodel", name: "Child", file: "child.py", instanceId: "Child", definitionId: "definition_child", readOnly: false },
  { type: "submodel", name: "Grandchild", file: "grandchild.py", instanceId: "Grandchild", definitionId: "definition_grandchild", readOnly: true },
]

// ── Tests ────────────────────────────────────────────────────────

describe("BreadcrumbBar", () => {
  it("returns null for a single-item viewStack", () => {
    const { container } = render(
      <BreadcrumbBar viewStack={[level("Only")]} onNavigate={vi.fn()} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("returns null for an empty viewStack", () => {
    const { container } = render(
      <BreadcrumbBar viewStack={[]} onNavigate={vi.fn()} />,
    )
    expect(container.innerHTML).toBe("")
  })

  it("renders level names for a multi-level stack", () => {
    render(<BreadcrumbBar viewStack={threeLevels} onNavigate={vi.fn()} />)

    expect(screen.getByText("Root")).toBeTruthy()
    expect(screen.getByText("Child")).toBeTruthy()
    expect(screen.getByText("Grandchild")).toBeTruthy()
  })

  it("marks a drilled instance as read-only", () => {
    render(<BreadcrumbBar viewStack={threeLevels} onNavigate={vi.fn()} />)

    expect(screen.getByText("Read-only instance")).toBeInTheDocument()
    expect(screen.getByTitle("Read-only submodel instance")).toBeInTheDocument()
  })

  it("renders viewStack.length - 1 chevron separators", () => {
    const { container } = render(
      <BreadcrumbBar viewStack={threeLevels} onNavigate={vi.fn()} />,
    )

    // ChevronRight renders as an <svg> with the lucide class
    const chevrons = container.querySelectorAll("svg.lucide-chevron-right")
    expect(chevrons.length).toBe(threeLevels.length - 1)
  })

  it("disables the last breadcrumb button", () => {
    render(<BreadcrumbBar viewStack={twoLevels} onNavigate={vi.fn()} />)

    const lastButton = screen.getByText("Sub")
    expect(lastButton).toBeDisabled()
  })

  it("does not disable earlier breadcrumb buttons", () => {
    render(<BreadcrumbBar viewStack={twoLevels} onNavigate={vi.fn()} />)

    const firstButton = screen.getByText("Main")
    expect(firstButton).not.toBeDisabled()
  })

  it("calls onNavigate with the correct index when clicking a non-last item", () => {
    const onNavigate = vi.fn()
    render(<BreadcrumbBar viewStack={threeLevels} onNavigate={onNavigate} />)

    fireEvent.click(screen.getByText("Root"))
    expect(onNavigate).toHaveBeenCalledWith(0)

    fireEvent.click(screen.getByText("Child"))
    expect(onNavigate).toHaveBeenCalledWith(1)

    expect(onNavigate).toHaveBeenCalledTimes(2)
  })

  it("does not call onNavigate when clicking the last (disabled) item", () => {
    const onNavigate = vi.fn()
    render(<BreadcrumbBar viewStack={twoLevels} onNavigate={onNavigate} />)

    fireEvent.click(screen.getByText("Sub"))
    expect(onNavigate).not.toHaveBeenCalled()
  })
})
