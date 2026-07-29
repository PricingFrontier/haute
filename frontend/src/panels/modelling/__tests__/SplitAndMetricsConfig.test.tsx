import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { SplitAndMetricsConfig } from "../SplitAndMetricsConfig"
import type { SplitAndMetricsConfigProps } from "../SplitAndMetricsConfig"

afterEach(cleanup)

const COLUMNS = [
  { name: "loss_amount", dtype: "Float64" },
  { name: "date_col", dtype: "Date" },
  { name: "group_id", dtype: "Utf8" },
  { name: "is_active", dtype: "Boolean" },
  { name: "age", dtype: "Int64" },
  { name: "exposure", dtype: "Float64" },
]

function makeProps(overrides: Partial<SplitAndMetricsConfigProps> = {}): SplitAndMetricsConfigProps {
  return {
    columns: COLUMNS,
    split: { strategy: "random", validation_size: 0.2, holdout_size: 0, seed: 42 },
    onSplitUpdate: vi.fn(),
    ...overrides,
  }
}

describe("SplitAndMetricsConfig", () => {
  it("renders split strategy buttons", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    expect(screen.getByText("random")).toBeInTheDocument()
    expect(screen.getByText("temporal")).toBeInTheDocument()
    expect(screen.getByText("group")).toBeInTheDocument()
  })

  it("highlights the active split strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    const randomBtn = screen.getByText("random")
    expect(randomBtn.style.color).toContain("var(--accent)")
  })

  it("clicking a split strategy calls onSplitUpdate", () => {
    const onSplitUpdate = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onSplitUpdate })} />)
    fireEvent.click(screen.getByText("temporal"))
    expect(onSplitUpdate).toHaveBeenCalledWith("strategy", "temporal")
  })

  it("shows validation/holdout/seed inputs for random strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps()} />)
    expect(screen.getByText("Validation")).toBeInTheDocument()
    expect(screen.getByText("Holdout")).toBeInTheDocument()
    expect(screen.getByText("Seed")).toBeInTheDocument()
  })

  it("shows date column and cutoff for temporal strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps({
      split: { strategy: "temporal", date_column: "", cutoff_date: "" },
    })} />)
    expect(screen.getByText("Date column")).toBeInTheDocument()
    expect(screen.getByText("Cutoff date")).toBeInTheDocument()
  })

  it("shows group column for group strategy", () => {
    render(<SplitAndMetricsConfig {...makeProps({
      split: { strategy: "group", group_column: "", validation_size: 0.2, holdout_size: 0 },
    })} />)
    expect(screen.getByText("Group column")).toBeInTheDocument()
  })

  it("changing validation size calls onSplitUpdate", () => {
    const onSplitUpdate = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onSplitUpdate })} />)
    const inputs = screen.getAllByRole("spinbutton")
    // First spinbutton is validation size
    fireEvent.change(inputs[0], { target: { value: "0.3" } })
    expect(onSplitUpdate).toHaveBeenCalledWith("validation_size", 0.3)
  })

})
