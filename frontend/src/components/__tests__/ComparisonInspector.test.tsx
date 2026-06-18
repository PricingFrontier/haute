import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup, within } from "@testing-library/react"

import ComparisonInspector from "../ComparisonInspector"
import type { ComparisonInspect } from "../ComparisonView"

afterEach(cleanup)

const changed: ComparisonInspect = {
  id: "rate",
  status: "changed",
  historical: { label: "Rate", nodeType: "polars", config: { factor: 1.1, note: "same" } },
  current: { label: "Rate", nodeType: "polars", config: { factor: 1.25, note: "same" } },
}

describe("ComparisonInspector", () => {
  it("shows the node label, a status badge, and the config heading", () => {
    render(<ComparisonInspector inspect={changed} onClose={vi.fn()} />)
    expect(screen.getByTestId("comparison-inspector")).toHaveTextContent("Rate")
    expect(screen.getByTestId("comparison-inspector-status")).toHaveTextContent("Changed")
    expect(screen.getByText("Configuration")).toBeInTheDocument()
  })

  it("renders old → new for a changed key and marks it changed", () => {
    render(<ComparisonInspector inspect={changed} onClose={vi.fn()} />)
    const rows = screen.getAllByTestId("comparison-inspector-row")
    const factorRow = rows.find((r) => within(r).queryByText("factor"))!
    expect(factorRow).toHaveAttribute("data-changed", "true")
    expect(factorRow).toHaveTextContent("1.1")
    expect(factorRow).toHaveTextContent("1.25")
    // An unchanged key is not flagged.
    const noteRow = rows.find((r) => within(r).queryByText("note"))!
    expect(noteRow).not.toHaveAttribute("data-changed")
  })

  it("shows only the current config for an added node", () => {
    const added: ComparisonInspect = {
      id: "fresh",
      status: "added",
      historical: null,
      current: { label: "Fresh", nodeType: "polars", config: { x: 5 } },
    }
    render(<ComparisonInspector inspect={added} onClose={vi.fn()} />)
    expect(screen.getByTestId("comparison-inspector-status")).toHaveTextContent("Added")
    const rows = screen.getAllByTestId("comparison-inspector-row")
    expect(rows).toHaveLength(1)
    expect(rows[0]).toHaveTextContent("x")
    expect(rows[0]).toHaveTextContent("5")
  })

  it("reports an empty config", () => {
    const empty: ComparisonInspect = {
      id: "bare",
      status: "unchanged",
      historical: { label: "Bare", nodeType: "polars", config: {} },
      current: { label: "Bare", nodeType: "polars", config: {} },
    }
    render(<ComparisonInspector inspect={empty} onClose={vi.fn()} />)
    expect(screen.getByTestId("comparison-inspector-empty")).toBeInTheDocument()
  })

  it("calls onClose from the panel header", () => {
    const onClose = vi.fn()
    render(<ComparisonInspector inspect={changed} onClose={onClose} />)
    fireEvent.click(screen.getByRole("button", { name: /close/i }))
    expect(onClose).toHaveBeenCalled()
  })
})
