import { describe, it, expect, vi, afterEach } from "vitest"
import { render, screen, fireEvent, cleanup } from "@testing-library/react"
import { SplitAndMetricsConfig } from "../SplitAndMetricsConfig"
import type { SplitAndMetricsConfigProps } from "../SplitAndMetricsConfig"

afterEach(cleanup)

const COLUMNS = [
  { name: "loss_amount", dtype: "Float64" },
  { name: "date_col", dtype: "Date" },
  { name: "group_id", dtype: "Utf8" },
  { name: "age", dtype: "Int64" },
]

function makeProps(overrides: Partial<SplitAndMetricsConfigProps> = {}): SplitAndMetricsConfigProps {
  return {
    config: {},
    onUpdate: vi.fn(),
    columns: COLUMNS,
    target: "loss_amount",
    weight: "",
    exclude: [],
    split: { strategy: "random", validation_size: 0.2, holdout_size: 0, seed: 42 },
    mlflowOpen: false,
    monotonicOpen: false,
    toggleSection: vi.fn(),
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

  it("MLflow section toggles on click", () => {
    const toggleSection = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ toggleSection })} />)
    fireEvent.click(screen.getByText("MLflow Logging"))
    expect(toggleSection).toHaveBeenCalledWith("modelling.mlflow")
  })

  it("shows MLflow fields when mlflowOpen is true", () => {
    render(<SplitAndMetricsConfig {...makeProps({ mlflowOpen: true })} />)
    expect(screen.getByText("Experiment path")).toBeInTheDocument()
    expect(screen.getByText(/Model name/)).toBeInTheDocument()
  })

  it("monotonic constraints section toggles on click", () => {
    const toggleSection = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ toggleSection })} />)
    fireEvent.click(screen.getByText("Monotonic Constraints"))
    expect(toggleSection).toHaveBeenCalledWith("modelling.monotonic")
  })

  it("shows monotonic constraint rows when open, excluding target/weight/string cols", () => {
    render(<SplitAndMetricsConfig {...makeProps({ monotonicOpen: true })} />)
    // age (Int64) should be visible, group_id (Utf8) and loss_amount (target) should not
    expect(screen.getByText("age")).toBeInTheDocument()
    expect(screen.queryByText("group_id")).not.toBeInTheDocument()
  })

  it("row limit input calls onUpdate", () => {
    const onUpdate = vi.fn()
    render(<SplitAndMetricsConfig {...makeProps({ onUpdate })} />)
    const rowLimitInput = screen.getByPlaceholderText("All rows")
    fireEvent.change(rowLimitInput, { target: { value: "50000" } })
    expect(onUpdate).toHaveBeenCalledWith("row_limit", 50000)
  })
})
