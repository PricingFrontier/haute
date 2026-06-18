import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"

// Stub the real editor render path — this suite verifies the inspector's wiring
// (status, which-version selector, which config it hands down, inert, close),
// not the editors themselves.
vi.mock("../ReadOnlyNodeConfig", () => ({
  default: ({ nodeType, config }: { nodeType: string; config: unknown }) => (
    <div data-testid="ro-config" data-nodetype={nodeType} data-config={JSON.stringify(config)} />
  ),
}))

import ComparisonInspector from "../ComparisonInspector"
import type { ComparisonInspect } from "../ComparisonView"

afterEach(cleanup)

const changed: ComparisonInspect = {
  id: "rate",
  status: "changed",
  historical: { label: "Rate", nodeType: "polars", config: { factor: 1.1 } },
  current: { label: "Rate", nodeType: "polars", config: { factor: 1.25 } },
}

describe("ComparisonInspector", () => {
  it("shows the label and a status badge", () => {
    render(<ComparisonInspector inspect={changed} onClose={vi.fn()} />)
    expect(screen.getByTestId("comparison-inspector")).toHaveTextContent("Rate")
    expect(screen.getByTestId("comparison-inspector-status")).toHaveTextContent("Changed")
  })

  it("renders the real editor read-only (inert) with the current config by default", () => {
    render(<ComparisonInspector inspect={changed} onClose={vi.fn()} />)
    expect(screen.getByTestId("comparison-inspector-config")).toHaveAttribute("inert")
    const ro = screen.getByTestId("ro-config")
    expect(ro).toHaveAttribute("data-nodetype", "polars")
    expect(ro.getAttribute("data-config")).toBe(JSON.stringify({ factor: 1.25 }))
  })

  it("offers a version switcher for a node present on both sides", () => {
    render(<ComparisonInspector inspect={changed} onClose={vi.fn()} />)
    // Default is current; switching to historical feeds the historical config.
    fireEvent.click(screen.getByTestId("comparison-inspector-view-historical"))
    expect(screen.getByTestId("ro-config").getAttribute("data-config")).toBe(
      JSON.stringify({ factor: 1.1 }),
    )
  })

  it("shows a single-version label for an added node (no switcher)", () => {
    const added: ComparisonInspect = {
      id: "fresh",
      status: "added",
      historical: null,
      current: { label: "Fresh", nodeType: "constant", config: { value: 5 } },
    }
    render(<ComparisonInspector inspect={added} onClose={vi.fn()} />)
    expect(screen.queryByTestId("comparison-inspector-view-historical")).not.toBeInTheDocument()
    expect(screen.getByTestId("comparison-inspector-only")).toHaveTextContent("Current pipeline")
    expect(screen.getByTestId("ro-config")).toHaveAttribute("data-nodetype", "constant")
  })

  it("shows the historical version for a removed node", () => {
    const removed: ComparisonInspect = {
      id: "gone",
      status: "removed",
      historical: { label: "Gone", nodeType: "polars", config: { code: "x" } },
      current: null,
    }
    render(<ComparisonInspector inspect={removed} onClose={vi.fn()} />)
    expect(screen.getByTestId("comparison-inspector-only")).toHaveTextContent("Historical version")
    expect(screen.getByTestId("ro-config").getAttribute("data-config")).toBe(
      JSON.stringify({ code: "x" }),
    )
  })

  it("calls onClose from the panel header", () => {
    const onClose = vi.fn()
    render(<ComparisonInspector inspect={changed} onClose={onClose} />)
    fireEvent.click(screen.getByRole("button", { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
